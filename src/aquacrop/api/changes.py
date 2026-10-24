"""Changes exported from the digital twin, on the real system (0.6.0, owner request).

The twin writes an export package to the feed directory (`aquacrop.twin.changes`). Here the real dashboard lists
each package with its differences against what the real system has now, its proof (the twin runs of every
program version) and its conflicts. An administrator, or an operator with commissioning (owner decision
2026-10-24), approves it; the changes are then applied through the same stores as a person on the Equipment and
Programs pages would use:

* equipment created or changed becomes a new item version; a retired item is retired;
* a changed channel assignment is PROPOSED: it must still be verified at the bench before a physical run;
* a program arrives as a new DRAFT version with the twin's proof recorded against it (same digest). It is not
  validated or activated here: that stays the normal step on the Programs and Operation pages.

Nothing is applied when the real system changed since the twin imported it (a conflict): import in the twin and
export again. Each applied package keeps an undo journal; the latest one can be undone while nothing it touched
has changed since. Undo never removes history: items get a new version, programs are retired.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..equipment.store import EquipmentRefused
from ..programs.store import ProgramRefused
from ..security.auth import Perm, Principal
from ..twin.changes import read_exports

LIVE_STORE = "LIVE"


class _B(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApproveIn(_B):
    digest: str = Field(pattern="^[0-9a-f]{64}$")
    note: str = Field(default="", max_length=1000)


class UndoIn(_B):
    digest: str = Field(pattern="^[0-9a-f]{64}$")
    note: str = Field(default="", max_length=1000)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def flat_diff(a, b, path: str = "", out: list | None = None, limit: int = 40) -> list[dict]:
    """Field-by-field differences between two JSON values, for the approver to read."""
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            flat_diff(a.get(k), b.get(k), f"{path}.{k}" if path else str(k), out, limit)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            flat_diff(x, y, f"{path}[{i}]", out, limit)
    elif a != b:
        out.append({"field": path or "(all)", "before": a, "after": b})
    return out


class Journal:
    """Applied packages and their undo steps: `<state>/twin-changes/applied/<digest16>.json`."""

    def __init__(self, directory: Path):
        self.dir = Path(directory) / "applied"

    def all(self) -> list[dict]:
        out = []
        if self.dir.is_dir():
            for f in self.dir.glob("*.json"):
                try:
                    out.append(json.loads(f.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    continue
        return sorted(out, key=lambda r: r["applied_utc"], reverse=True)

    def get(self, digest: str) -> dict | None:
        return next((r for r in self.all() if r["digest"] == digest), None)

    def save(self, rec: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{rec['digest'][:16]}.json"
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(rec, indent=1, sort_keys=True, default=str), encoding="utf-8")
        os.replace(tmp, path)


def register(app, require, equipment, programs, feed_dir: Path | None, state_dir: Path, hist, can_approve, publish):
    journal = Journal(Path(state_dir) / "twin-changes")

    def live_items() -> dict[str, dict]:
        return {i["item_id"]: i for i in equipment.items()}

    def live_point(iid: str, role: str) -> str | None:
        b = equipment.bindings(iid).get(role)
        return b["point"] if b else None

    def latest_program(pid: str) -> dict | None:
        vs = [v for v in programs.versions() if v["program_id"] == pid]
        return vs[-1] if vs else None

    def active_anywhere(pid: str, version: int) -> list[str]:
        rows = hist.query("SELECT a.runtime_id, a.version, a.action FROM program_activations a JOIN "
                          "(SELECT runtime_id, max(seq) AS seq FROM program_activations WHERE program_id=? "
                          "GROUP BY runtime_id) m ON a.seq=m.seq", (pid,))
        return [r["runtime_id"] for r in rows if r["action"] == "ACTIVATED" and r["version"] == version]

    def conflicts(pkg: dict) -> list[str]:
        out = []
        items = live_items()
        types = {(t["type_id"], t["version"]): t for t in equipment.types()}
        pkg_items = {i["item_id"] for i in pkg["items"]}
        for i in pkg["items"]:
            cur = items.get(i["item_id"])
            if i["op"] == "create":
                if cur is not None:
                    out.append(f"{i['item_id']} now exists on the real system")
            elif cur is None or cur["version"] != i["base_version"] or cur["status"] == "RETIRED":
                out.append(f"{i['name']} changed on the real system since the twin imported it")
            if i["op"] in ("create", "update"):
                t = types.get((i["type_id"], i["type_version"]))
                if t is None or t["status"] not in ("ACTIVE", "SUPERSEDED"):
                    out.append(f"{i['name']}: type {i['type_id']} v{i['type_version']} is not active here")
            if i["op"] == "retire":
                users = [u for u in programs.dependents(i["item_id"])]
                if users:
                    out.append(f"{i['name']} is still named by program versions here ({', '.join(users)})")
        by_point = {v["point"]: v for v in equipment.bindings().values()}
        for b in pkg["bindings"]:
            if b["item_id"] not in items and b["item_id"] not in pkg_items:
                out.append(f"{b['item_id']} does not exist here")
                continue
            if b["item_id"] in items and live_point(b["item_id"], b["role"]) != b["base_point"]:
                out.append(f"the channel of {b['item_id']}.{b['role']} changed on the real system")
            other = by_point.get(b["point"]) if b["point"] else None
            if other and (other["item_id"], other["role"]) != (b["item_id"], b["role"]) and not any(
                    (x["item_id"], x["role"]) == (other["item_id"], other["role"]) for x in pkg["bindings"]):
                out.append(f"{b['point']} carries {other['item_id']}.{other['role']} on the real system")
        for p in pkg["programs"]:
            cur = latest_program(p["program_id"])
            if (cur["version"] if cur else None) != p["base_version"]:
                out.append(f"{p['name']} has a newer version on the real system (v{cur['version']})")
            elif cur is not None and cur["status"] == "DRAFT":
                out.append(f"{p['name']} has an unfinished draft on the real system (v{cur['version']})")
        return out

    def describe(pkg: dict) -> dict:
        items = []
        for i in pkg["items"]:
            d = {"item_id": i["item_id"], "op": i["op"], "name": i["name"], "exercised_by": i.get("exercised_by", [])}
            if i["op"] == "update":
                after = {k: i[k] for k in ("name", "type_id", "type_version", "icon", "body")}
                d["diff"] = flat_diff(i["before"], after)
            elif i["op"] == "create":
                d["detail"] = f"type {i['type_id']} v{i['type_version']}, icon {i['icon']}"
            items.append(d)
        progs = []
        for p in pkg["programs"]:
            progs.append({"program_id": p["program_id"], "name": p["name"], "base_version": p["base_version"],
                          "twin_version": p["twin_version"], "digest": p["digest"],
                          "diff": flat_diff(p.get("base_body") or {}, p["body"]) if p.get("base_body") else None,
                          "body": p["body"], "passed": p["passed"], "failed": p.get("failed", 0),
                          "runs": [{k: r.get(k) for k in ("result", "reason", "ended_utc", "measured_L", "requested_L")}
                                   for r in p.get("runs", [])][-5:]})
        return {"digest": pkg["digest"], "by": pkg["by"], "note": pkg["note"], "written_utc": pkg["written_utc"],
                "twin_run": pkg["twin_run"], "items": items, "bindings": pkg["bindings"], "programs": progs,
                "meaning": pkg.get("meaning"), "conflicts": conflicts(pkg)}

    def package(digest: str) -> dict:
        pkg = next((x for x in (read_exports(feed_dir) if feed_dir else []) if x["digest"] == digest), None)
        if pkg is None:
            raise HTTPException(404, "no such export from the twin")
        return pkg

    @app.get("/api/twin-changes")
    def view(p: Principal = Depends(require(Perm.READ))):
        applied = journal.all()
        done = {r["digest"] for r in applied}
        pending = [describe(x) for x in (read_exports(feed_dir) if feed_dir else []) if x["digest"] not in done][:10]
        latest = next((r for r in applied if not r.get("undone_utc")), None)
        return {"pending": pending, "can_approve": can_approve(p),
                "applied": [{k: r.get(k) for k in ("digest", "by", "approved_by", "applied_utc", "note", "undone_utc",
                                                    "undone_by", "summary")} for r in applied[:10]],
                "undo_digest": latest["digest"] if latest else None}

    # ------------------------------------------------------------------ apply
    def apply(pkg: dict, actor: str) -> list[dict]:
        steps: list[dict] = []
        tag = f"twin export {pkg['digest'][:12]}"
        try:
            for i in pkg["items"]:
                if i["op"] in ("create", "update"):
                    it = equipment.save_item(actor, i["item_id"], i["name"], i["type_id"], i["type_version"], i["icon"],
                                             i["body"], expected_version=i["base_version"])
                    steps.append({"op": "item_" + ("created" if i["op"] == "create" else "updated"),
                                  "item_id": i["item_id"], "before": i.get("before"), "version_after": it["version"]})
            for b in pkg["bindings"]:
                before = live_point(b["item_id"], b["role"])
                if before != b["point"]:
                    equipment.bind(actor, b["item_id"], b["role"], b["point"], note=tag)
                    steps.append({"op": "bound", "item_id": b["item_id"], "role": b["role"], "before": before,
                                  "after": b["point"]})
            for p in pkg["programs"]:
                v = programs.save_draft(actor, p["program_id"], p["name"], p["body"])
                if v["digest"] != p["digest"]:
                    raise ProgramRefused(f"{p['program_id']}: the saved body differs from the exported one")
                programs.record_simulation(p["program_id"], v["version"], {
                    "kind": "AQUACROP_TWIN_EVIDENCE", "program_id": p["program_id"], "version": v["version"],
                    "digest": p["digest"], "passed": True, "accepted_by": pkg["by"], "accepted_utc": pkg["written_utc"],
                    "twin_run_id": pkg["twin_run"], "runs": p.get("runs", []), "note": pkg["note"],
                    "meaning": pkg.get("meaning"), "export": pkg["digest"], "twin_version": p["twin_version"]})
                steps.append({"op": "program_added", "program_id": p["program_id"], "version": v["version"]})
            for i in pkg["items"]:
                if i["op"] == "retire":
                    binds = {r: b["point"] for r, b in equipment.bindings(i["item_id"]).items()}
                    it = equipment.retire_item(actor, i["item_id"], tag)
                    steps.append({"op": "item_retired", "item_id": i["item_id"], "before": i["before"],
                                  "bindings": binds, "version": it["version"]})
        except (EquipmentRefused, ProgramRefused, ValueError) as e:
            undo_steps(steps, actor, f"{tag} failed")
            raise HTTPException(409, f"nothing was changed: {e}")
        return steps

    def undo_blockers(steps: list[dict]) -> list[str]:
        """Undo only what is still as the export left it."""
        problems = []
        items = live_items()
        for s in steps:
            if s["op"] in ("item_created", "item_updated"):
                cur = items.get(s["item_id"])
                if cur is None or cur["version"] != s["version_after"] or cur["status"] == "RETIRED":
                    problems.append(f"{s['item_id']} changed after the export was applied")
            elif s["op"] == "item_retired":
                cur = items.get(s["item_id"])
                if cur is None or cur["version"] != s["version"] or cur["status"] != "RETIRED":
                    problems.append(f"{s['item_id']} changed after the export was applied")
            elif s["op"] == "bound" and live_point(s["item_id"], s["role"]) != s["after"]:
                problems.append(f"the channel of {s['item_id']}.{s['role']} changed after the export was applied")
            elif s["op"] == "program_added":
                rt = active_anywhere(s["program_id"], s["version"])
                if rt:
                    problems.append(f"{s['program_id']} v{s['version']} is active ({', '.join(rt)}): deactivate it first")
        return problems

    def undo_steps(steps: list[dict], actor: str, why: str) -> list[str]:
        problems = []
        for s in reversed(steps):
            try:
                if s["op"] == "item_retired":
                    b = s["before"]
                    equipment.save_item(actor, s["item_id"], b["name"], b["type_id"], b["type_version"], b["icon"], b["body"],
                                        expected_version=s["version"], reactivate=True)
                    for role, point in s.get("bindings", {}).items():
                        equipment.bind(actor, s["item_id"], role, point, note=why)
                elif s["op"] == "program_added":
                    hist.submit(lambda c, s=s: c.execute(
                        "UPDATE programs SET status='RETIRED', updated_utc=? WHERE program_id=? AND version=?",
                        (_now(), s["program_id"], s["version"])), wait=True)
                    hist.event("PROGRAM_RETIRED", {"program": f"{s['program_id']}@{s['version']}", "why": why}, actor=actor)
                elif s["op"] == "bound":
                    equipment.bind(actor, s["item_id"], s["role"], s["before"], note=why)
                elif s["op"] == "item_updated":
                    b = s["before"]
                    cur = live_items()[s["item_id"]]["version"]
                    equipment.save_item(actor, s["item_id"], b["name"], b["type_id"], b["type_version"], b["icon"], b["body"],
                                        expected_version=cur)
                elif s["op"] == "item_created":
                    equipment.retire_item(actor, s["item_id"], why)
            except (EquipmentRefused, ProgramRefused, ValueError) as e:
                problems.append(f"{s['op']} {s.get('item_id') or s.get('program_id')}: {e}")
        return problems

    def summary(pkg: dict) -> str:
        n = {"create": 0, "update": 0, "retire": 0}
        for i in pkg["items"]:
            n[i["op"]] += 1
        return (f"{n['create']} created, {n['update']} changed, {n['retire']} retired equipment; "
                f"{len(pkg['bindings'])} channel(s); {len(pkg['programs'])} program version(s)")

    @app.post("/api/twin-changes/approve")
    def approve(body: ApproveIn, p: Principal = Depends(require(Perm.READ))):
        if not can_approve(p):
            raise HTTPException(403, "only administrators and operators with commissioning approve changes from the twin")
        if journal.get(body.digest):
            raise HTTPException(409, "this export was applied already")
        pkg = package(body.digest)
        c = conflicts(pkg)
        if c:
            raise HTTPException(409, "the real system changed since the twin imported it: " + "; ".join(c)
                                + ". Import in the twin and export again.")
        if any(not x["passed"] for x in pkg["programs"]):
            raise HTTPException(409, "a program in this export has no twin run that ended DONE")
        if any(x.get("failed") for x in pkg["programs"]) and not body.note.strip():
            raise HTTPException(409, "some twin runs of these programs did not end DONE: say why you approve anyway")
        steps = apply(pkg, p.username)
        rec = {"digest": pkg["digest"], "by": pkg["by"], "approved_by": p.username, "applied_utc": _now(),
               "note": body.note, "steps": steps, "summary": summary(pkg), "twin_run": pkg["twin_run"]}
        journal.save(rec)
        hist.event("TWIN_EXPORT_APPLIED", {"digest": pkg["digest"], "summary": rec["summary"], "note": body.note,
                                           "exported_by": pkg["by"]}, actor=p.username)
        publish()
        return {k: rec[k] for k in ("digest", "applied_utc", "summary")}

    @app.post("/api/twin-changes/undo")
    def undo(body: UndoIn, p: Principal = Depends(require(Perm.READ))):
        if not can_approve(p):
            raise HTTPException(403, "only administrators and operators with commissioning undo changes from the twin")
        rec = journal.get(body.digest)
        if rec is None or rec.get("undone_utc"):
            raise HTTPException(404, "no applied export to undo with this digest")
        latest = next((r for r in journal.all() if not r.get("undone_utc")), None)
        if latest["digest"] != rec["digest"]:
            raise HTTPException(409, "undo the most recent export first")
        blockers = undo_blockers(rec["steps"])
        if blockers:
            raise HTTPException(409, "cannot undo: " + "; ".join(blockers))
        problems = undo_steps(rec["steps"], p.username, f"undo of twin export {rec['digest'][:12]}")
        rec.update(undone_utc=_now(), undone_by=p.username, undo_note=body.note, undo_problems=problems)
        journal.save(rec)
        hist.event("TWIN_EXPORT_UNDONE", {"digest": rec["digest"], "problems": problems, "note": body.note},
                   actor=p.username)
        publish()
        return {"digest": rec["digest"], "problems": problems}
