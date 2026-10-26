"""HTTP routes of the program editor on the live service (0.5.0).

Programs are written here (the source of truth) and copied to the twin with the live snapshot. Saving or
validating a program executes nothing. Validation records two lists: problems of the program itself, which block
it, and what a physical run would still need (verified channels, real scaling), which only the bench-test
profile's activation enforces. Twin evidence files are read on every list and recorded against the same version
only when the digest matches.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..equipment.definitions import TypeBody
from ..programs.definitions import actuators, errors_text, parse, validate
from ..programs.proof import refusal as proof_refusal
from ..programs.schedule import overlap, upcoming
from ..programs.store import ProgramRefused
from ..security.auth import Perm, Principal
from ..twin import feed


class _B(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DraftIn(_B):
    program_id: str = Field(max_length=48)
    name: str = Field(max_length=80)
    body: dict
    version: int | None = Field(default=None, ge=1)


class RefIn(_B):
    program_id: str = Field(max_length=48)
    version: int = Field(ge=1)


class FormIn(_B):
    program_id: str = Field(max_length=48)
    name: str = Field(max_length=80)
    form: dict
    version: int | None = Field(default=None, ge=1)


class PreviewIn(_B):
    form: dict


def describe(body: dict) -> str:
    """Plain words for a program written before the forms (its definition is kept as it is)."""
    t, a = body["trigger"], body["action"]
    when = {"MANUAL": "when started by hand", "CONDITION": "when a sensor condition holds",
            "CONTINUOUS": "continuously"}.get(t["type"]) or "; ".join(
        f"{','.join(e['days'])} {e['start']}-{e['end']}" for e in t.get("entries", []))
    what = {"RUN_FOR": lambda: f"run {a['item']} " + (f"for {a['duration_s']:g} s" if a.get("duration_s") else "for the window"),
            "DELIVER_VOLUME": lambda: f"deliver {a['target_L']:g} L with {a['item']}, measured by {a['meter']}",
            "KEEP_BETWEEN": lambda: f"keep {a['sensor']} between {a['low']:g} and {a['high']:g} with {a['item']}",
            "SEQUENCE": lambda: f"{len(a['steps'])} steps (at most {a['max_runtime_s']:g} s)"}[a["type"]]()
    return f"{when}: {what}"


def step_words(s: dict, names: dict) -> str:
    n = lambda i: names.get(i, i)                     # noqa: E731
    return {"ON": lambda: f"switch on {n(s['item'])}" + (f" and wait up to {s['timeout_s']:g} s for its {s['verify_role']} feedback" if s.get("verify_role") else ""),
            "OFF": lambda: f"switch off {n(s['item'])} and verify it is off",
            "WAIT": lambda: f"wait {s['seconds']:g} s",
            "RUN": lambda: f"keep watering for {s['seconds'] / 60:g} min",
            "DELIVER": lambda: f"deliver {s['target_L']:g} L measured by {n(s['meter'])} (stop if no flow for {s['no_flow_timeout_s']:g} s)"}[s["do"]]()


def stop_words(plan: list | None, names: dict, when: str) -> str:
    rest = "every item still on is switched off, the last one first, and each stop is verified; an alarm says why"
    if not plan:
        return f"{when}: {rest}"
    return (f"{when}: " + ", then ".join(step_words(s, names) for s in plan)
            + "; each step waits for the previous stop to be verified. Then " + rest.replace("every item", "any other item"))


def register(app, require, programs, equipment, cfg, feed_dir: Path | None, publish, *, blocks=None, views=None,
             proof_context=None) -> None:
    def context():
        items = {i["item_id"]: i for i in equipment.items()}
        types = {(t["type_id"], t["version"]): TypeBody.model_validate(t["body"]) for t in equipment.types()}
        bindings: dict[str, dict] = {}
        for v in equipment.bindings().values():
            bindings.setdefault(v["item_id"], {})[v["role"]] = v
        readiness = {iid: equipment.readiness(iid) for iid, it in items.items() if it["status"] == "ACTIVE"}
        return items, types, bindings, readiness

    def import_evidence() -> None:
        if feed_dir is None:
            return
        have = {(v["program_id"], v["version"]): v for v in programs.versions()}
        for ev in feed.read_evidence(feed_dir):
            v = have.get((ev.get("program_id"), ev.get("version")))
            if v is None or v["digest"] != ev.get("digest") or (v["simulation"] or {}).get("accepted_utc") == ev.get("accepted_utc"):
                continue
            try:
                programs.record_simulation(v["program_id"], v["version"], ev)
            except ProgramRefused:
                continue

    def names() -> dict[str, str]:
        return {i["item_id"]: i["name"] for i in equipment.items()}

    def block_list() -> list[dict]:
        if blocks is None:
            return []
        out = []
        nm = names()
        for b in blocks.latest().values():
            body = b["body"]
            out.append({"block_id": b["block_id"], "version": b["version"], "status": "STALE" if blocks.stale(b) else b["status"],
                        "name": b["name"], "body": body,
                        "summary": f"from {nm.get(body.get('supply'), body.get('supply'))} to "
                                   f"{nm.get(body.get('destination'), body.get('destination'))}"
                                   + (", measured" if body.get("meter") else ", timed only")})
        return out

    def readings() -> list[dict]:
        out = []
        for iid, v in sorted((views() if views else {}).items()):
            for r in v.type.roles:
                if r.direction.value != "INPUT":
                    continue
                sc_ = v.body.scaling.get(r.role)
                out.append({"item_id": iid, "name": v.name, "role": r.role, "label": r.label, "signal": r.signal.value,
                            "unit": (sc_.unit if sc_ else r.unit) if r.signal.value == "ANALOG" else None})
        return out

    def compile_request(form: dict):
        """(ProgramBody dict, WateringForm, block row) or a JSONResponse naming the field to correct."""
        from ..blocks.definitions import parse as parse_block
        from ..programs.authoring import FormRefused, WateringForm, compile_form
        try:
            f = WateringForm.model_validate(form)
        except ValidationError as e:
            err = e.errors()[0]
            return JSONResponse({"detail": {"message": err.get("msg", "").removeprefix("Value error, "),
                                            "field": ".".join(str(x) for x in err.get("loc", ()))}}, status_code=422)
        if blocks is None:
            return JSONResponse({"detail": "blocks are not available here"}, status_code=409)
        try:
            b = blocks.get(f.block_id, f.block_version)
        except Exception as e:
            return JSONResponse({"detail": {"message": str(e), "field": "block_id"}}, status_code=409)
        if blocks.stale(b):
            return JSONResponse({"detail": {"message": f"{b['name']}: the installation's connections changed since it was "
                                                       "validated; validate the block again on Blocks", "field": "block_id"}},
                                status_code=409)
        try:
            body = compile_form(f, parse_block(b["body"]), b["status"], views() if views else {})
            parse(body)
        except FormRefused as e:
            return JSONResponse({"detail": {"message": str(e), "field": e.field}}, status_code=409)
        except ValidationError as e:
            return JSONResponse({"detail": {"message": "; ".join(errors_text(e)), "field": None}}, status_code=409)
        return body, f, b

    @app.post("/api/programs/preview")
    def program_preview(body: PreviewIn, p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        from ..programs.authoring import summary
        r = compile_request(body.form)
        if isinstance(r, JSONResponse):
            return r
        pbody, f, b = r
        nm = names()
        pb = parse(pbody)
        items, types, bindings, readiness = context()
        logic = validate(pb, items, types, physical=False, bindings=bindings, readiness=readiness)
        physical = [x for x in validate(pb, items, types, physical=True, bindings=bindings, readiness=readiness)
                    if x not in logic]
        mine = set(actuators(pb))
        conflicts = sorted({f"{o['name']} ({', '.join(sorted(mine & set(actuators(parse(o['body'])))))})"
                            for o in programs.versions() if o["status"] == "VALIDATED"
                            and mine & set(actuators(parse(o["body"])))})
        return {"summary": summary(f, b["name"], nm, cfg.site.timezone, datetime.now(timezone.utc)),
                "steps": [step_words(s, nm) for s in pbody["action"]["steps"]],
                # R07-02: the plans the engine follows, in the operator's words (programs/engine.py _choose_plan)
                "fault_stop": stop_words(pbody["action"].get("fault_stop"), nm, "after a failure or a stop condition"),
                "interrupted_stop": stop_words(pbody["action"].get("normal_stop"), nm,
                                               "when someone stops it, or its guard time or window ends"),
                "resources": [nm.get(i, i) for i in sorted(mine)], "conflicts": conflicts,
                "issues": logic, "physical_needs": physical, "body": pbody}

    @app.post("/api/programs/form")
    def program_form(body: FormIn, p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        r = compile_request(body.form)
        if isinstance(r, JSONResponse):
            return r
        pbody, _, _ = r
        try:
            v = programs.save_draft(p.username, body.program_id, body.name, pbody, version=body.version)
        except ProgramRefused as e:
            return JSONResponse({"detail": {"message": str(e), "field": "program_id" if "id" in str(e) else "name"}},
                                status_code=409)
        publish()
        return v

    @app.get("/api/programs")
    def program_list(p: Principal = Depends(require(Perm.READ))):
        from ..programs.authoring import WateringForm, summary
        import_evidence()
        now = datetime.now(timezone.utc)
        out = []
        nm = names()
        bl = {b["block_id"]: b for b in block_list()}
        ops = p.can(Perm.OPS_READ)
        for v in programs.versions():
            body = v["body"]
            nxt = []
            if body["trigger"]["type"] in ("WEEKLY", "WINDOW_REPEAT"):
                nxt = [w.start_local for w in upcoming(f"{v['program_id']}@{v['version']}", body["trigger"]["entries"],
                                                        cfg.site.timezone, now, 3)]
            form = body.get("authoring")
            words = None
            if form:
                try:
                    f = WateringForm.model_validate(form)
                    words = summary(f, (bl.get(f.block_id) or {}).get("name", f.block_id), nm, cfg.site.timezone, now)
                except ValidationError:
                    words = None
            sim = None
            if v["simulation"]:
                # R07-01: a passed result counts only in this version's proof context on this system now
                sim = {k: v["simulation"].get(k) for k in ("passed", "accepted_by", "accepted_utc", "meaning")}
                if proof_context is not None:
                    why = proof_refusal(v["simulation"], v["digest"], proof_context(v["program_id"], v["version"]))
                    sim |= {"current": why is None, "why_not_current": why}
            row = {k: v[k] for k in ("program_id", "version", "status", "name", "digest", "updated_utc")} | {
                "next": nxt, "form": form, "summary": words, "describe": describe(body), "simulation": sim}
            if ops:                                  # a viewer reads summaries, not definitions or readiness details
                row |= {"body": body, "validation": v["validation"], "created_by": v["created_by"],
                        "simulation": None if sim is None else v["simulation"] | sim}
            out.append(row)
        if not ops:
            return {"programs": out, "timezone": cfg.site.timezone, "blocks": [], "readings": [], "items": []}
        items, types, *_ = context()
        return {"programs": out, "timezone": cfg.site.timezone, "blocks": block_list(), "readings": readings(),
                "items": [{"item_id": i["item_id"], "name": i["name"], "type_id": i["type_id"],
                           "roles": [r.model_dump(mode="json") for r in types[(i["type_id"], i["type_version"])].roles],
                           "operations": [o.value for o in types[(i["type_id"], i["type_version"])].operations]}
                          for i in items.values() if i["status"] == "ACTIVE"],
                "live_activation": ("not available on this profile: the real system is read-only. Physical bench "
                                    "tests need the bench-test profile and an armed test session.")}

    @app.post("/api/programs/draft")
    def draft(body: DraftIn, p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        try:
            r = programs.save_draft(p.username, body.program_id, body.name, body.body, version=body.version)
        except ProgramRefused as e:
            raise HTTPException(409, str(e))
        publish()
        return r

    @app.post("/api/programs/validate")
    def validate_route(body: RefIn, p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        try:
            v = programs.get(body.program_id, body.version)
            pb = parse(v["body"])
        except ProgramRefused as e:
            raise HTTPException(409, str(e))
        items, types, bindings, readiness = context()
        logic = validate(pb, items, types, physical=False, bindings=bindings, readiness=readiness)
        physical = [x for x in validate(pb, items, types, physical=True, bindings=bindings, readiness=readiness)
                    if x not in logic]
        overlaps = []
        t = pb.trigger
        if t.type == "WEEKLY":
            mine = set(actuators(pb))
            for other in programs.versions():
                if other["program_id"] == body.program_id or other["status"] != "VALIDATED":
                    continue
                ob = parse(other["body"])
                if ob.trigger.type == "WEEKLY" and mine & set(actuators(ob)):
                    overlaps += [f"{other['program_id']}: {x}" for x in overlap(
                        [e.model_dump() for e in t.entries], [e.model_dump() for e in ob.trigger.entries],
                        cfg.site.timezone, from_day=date.today(), days=14)]
        try:
            r = programs.mark_validated(p.username, body.program_id, body.version, logic,
                                        {"physical_needs": physical, "overlaps": overlaps[:20]})
        except ProgramRefused as e:
            raise HTTPException(409, str(e))
        publish()
        return r | {"issues": logic, "physical_needs": physical, "overlaps": overlaps[:20]}
