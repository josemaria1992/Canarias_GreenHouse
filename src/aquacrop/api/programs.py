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
from pydantic import BaseModel, ConfigDict, Field

from ..equipment.definitions import TypeBody
from ..programs.definitions import actuators, parse, validate
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


def register(app, require, programs, equipment, cfg, feed_dir: Path | None, publish) -> None:
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

    @app.get("/api/programs")
    def program_list(p: Principal = Depends(require(Perm.READ))):
        import_evidence()
        now = datetime.now(timezone.utc)
        out = []
        for v in programs.versions():
            body = v["body"]
            nxt = []
            if body["trigger"]["type"] == "WEEKLY":
                nxt = [w.start_local for w in upcoming(f"{v['program_id']}@{v['version']}", body["trigger"]["entries"],
                                                        cfg.site.timezone, now, 3)]
            out.append({k: v[k] for k in ("program_id", "version", "status", "name", "digest", "created_by",
                                          "updated_utc", "body", "validation", "simulation")} | {"next": nxt})
        items, types, *_ = context()
        return {"programs": out, "timezone": cfg.site.timezone,
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
