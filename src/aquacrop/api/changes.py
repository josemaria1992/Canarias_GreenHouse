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

Durability (0.7.0, FB-07). The stores commit each step separately, so an apply is not one database transaction.
Instead the journal is written before anything changes (state APPLYING) and records each step's intent before it
is made and its result after; one approval at a time holds the apply lock, and a package already journaled in any
state cannot be applied again. A failure undoes the steps made, and the answer says "nothing was changed" only when
every undo worked; otherwise the record is PARTIAL with each problem. After a restart a record left APPLYING or
UNDOING is INTERRUPTED: each step whose result was not recorded is checked against what the real system has
(done / not done), shown to the approver, and can be undone. Nothing is resumed automatically.

Journal write failures (review R07-04). A failed apply is compensated (state COMPENSATING, journaled before the
undo when the journal can be written), and a record that cannot be written stays in memory, so this process
reports the truth and says the record is unsaved. At the next start every step recorded DONE is checked again
against the real system, not only those left at INTENT: a step the compensation already took back is UNDONE, and
one that is neither as applied nor as before is UNKNOWN and blocks an undo until someone checks it. On Windows a
replace refused because another process has the file open is retried briefly before it counts as a failure.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..blocks.store import BlockRefused
from ..equipment.store import EquipmentRefused
from ..programs.store import ProgramRefused
from ..security.auth import Perm, Principal
from ..twin.changes import read_exports
from ..twin.feed import replace

LIVE_STORE = "LIVE"


class _B(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApproveIn(_B):
    digest: str = Field(pattern="^[0-9a-f]{64}$")
    note: str = Field(default="", max_length=1000)


class DismissIn(_B):
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
        self.unsaved: dict[str, dict] = {}       # digest -> a record this process could not write (R07-04)

    def all(self) -> list[dict]:
        out = {}
        if self.dir.is_dir():
            for f in self.dir.glob("*.json"):
                try:
                    r = json.loads(f.read_text(encoding="utf-8"))
                    out[r["digest"]] = r
                except (OSError, ValueError, KeyError):
                    continue
        for d, r in list(self.unsaved.items()):   # what this process knows beats a stale file
            try:
                self.save(r)
            except OSError:
                out[d] = r | {"journal_unsaved": True}
            else:
                out[d] = r
        return sorted(out.values(), key=lambda r: r["applied_utc"], reverse=True)

    def get(self, digest: str) -> dict | None:
        return next((r for r in self.all() if r["digest"] == digest), None)

    def dismissed(self) -> set[str]:
        """Exports that can no longer be applied and that someone set aside (0.6.10)."""
        d = self.dir.parent / "dismissed"
        out = set()
        if d.is_dir():
            for f in d.glob("*.json"):
                try:
                    out.add(json.loads(f.read_text(encoding="utf-8"))["digest"])
                except (OSError, ValueError, KeyError):
                    continue
        return out

    def dismiss(self, rec: dict) -> None:
        self.save(rec, folder=self.dir.parent / "dismissed")

    def save(self, rec: dict, folder: Path | None = None, *, keep: bool = True) -> None:
        """Write the record atomically. When that fails the error is raised and, with `keep`, the record stays in
        memory as this process's truth (listed with journal_unsaved and written again at the next listing)."""
        folder = folder or self.dir
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{rec['digest'][:16]}.json"
            tmp = path.with_name(path.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(rec, indent=1, sort_keys=True, default=str), encoding="utf-8")
            replace(tmp, path)
        except OSError:
            if keep and folder == self.dir:
                self.unsaved[rec["digest"]] = json.loads(json.dumps(rec, default=str))
            raise
        if folder == self.dir:
            self.unsaved.pop(rec["digest"], None)

    def forget_unsaved(self, digest: str) -> None:
        self.unsaved.pop(digest, None)



def register(app, require, equipment, programs, feed_dir: Path | None, state_dir: Path, hist, can_approve, publish,
             blocks=None):
    journal = Journal(Path(state_dir) / "twin-changes")
    apply_lock = threading.Lock()                 # one apply or undo at a time, across every session

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
        hyd = pkg.get("hydraulics")
        if hyd:
            if blocks is None:
                out.append("the real system has no installation graph store")
            else:
                if blocks.topology()["rev"] != hyd["topology"]["base_rev"]:
                    out.append("the installation graph changed on the real system since the twin imported it")
                if blocks.diagram("installation")["rev"] != hyd["diagram"]["base_rev"]:
                    out.append("the installation drawing changed on the real system since the twin imported it")
                latest = blocks.latest()
                for b in hyd["blocks"]:
                    cur = latest.get(b["block_id"])
                    if cur is not None and cur["status"] == "DRAFT":
                        out.append(f"block {b['name']} has an unfinished draft on the real system")
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
        hyd = pkg.get("hydraulics")
        hyd_view = None if not hyd else {
            "graph_changed": hyd["graph_changed"], "drawing_changed": hyd["drawing_changed"], "meaning": hyd["meaning"],
            "nodes": len(hyd["topology"]["graph"].get("nodes", [])), "edges": len(hyd["topology"]["graph"].get("edges", [])),
            "blocks": [b["name"] for b in hyd["blocks"]]}
        return {"digest": pkg["digest"], "by": pkg["by"], "note": pkg["note"], "written_utc": pkg["written_utc"],
                "twin_run": pkg["twin_run"], "items": items, "bindings": pkg["bindings"], "programs": progs,
                "hydraulics": hyd_view, "meaning": pkg.get("meaning"), "conflicts": conflicts(pkg)}

    def package(digest: str) -> dict:
        pkg = next((x for x in (read_exports(feed_dir) if feed_dir else []) if x["digest"] == digest), None)
        if pkg is None:
            raise HTTPException(404, "no such export from the twin")
        return pkg

    @app.get("/api/twin-changes")
    def view(p: Principal = Depends(require(Perm.OPS_READ))):
        applied = journal.all()
        done = {r["digest"] for r in applied if r.get("state") != "FAILED_NOTHING_CHANGED"} | journal.dismissed()
        pending = [describe(x) for x in (read_exports(feed_dir) if feed_dir else []) if x["digest"] not in done][:10]
        latest = next((r for r in applied if not r.get("undone_utc") and r.get("state") != "FAILED_NOTHING_CHANGED"),
                      None)
        return {"pending": pending, "can_approve": can_approve(p),
                "applied": [{k: r.get(k) for k in ("digest", "by", "approved_by", "applied_utc", "note", "undone_utc",
                                                    "undone_by", "summary", "error", "undo_problems", "interrupted",
                                                    "journal_unsaved", "journal_errors")}
                            | {"state": r.get("state", "APPLIED")} for r in applied[:10]],
                "attention": [{"digest": r["digest"], "state": r["state"], "interrupted": r.get("interrupted"),
                               "problems": r.get("undo_problems")} for r in applied
                              if r.get("state") in ("INTERRUPTED", "PARTIAL") or r.get("journal_unsaved")],
                "undo_digest": latest["digest"] if latest else None}

    # ------------------------------------------------------------------ apply
    def apply(pkg: dict, actor: str, rec: dict) -> list[dict]:
        """Each step is journaled as INTENT before it is made and DONE (with what it produced) after."""
        steps: list[dict] = rec["steps"]
        tag = f"twin export {pkg['digest'][:12]}"

        def intent(step: dict) -> dict:
            step["state"] = "INTENT"
            steps.append(step)
            journal.save(rec)
            fail_point("intent", step)
            return step

        def done(step: dict, **result) -> None:
            fail_point("before_done", step)
            step.update(result, state="DONE")
            journal.save(rec)
        try:
            for i in pkg["items"]:
                if i["op"] in ("create", "update"):
                    st = intent({"op": "item_" + ("created" if i["op"] == "create" else "updated"),
                                 "item_id": i["item_id"], "before": i.get("before"), "base_version": i["base_version"],
                                 "target": {k: i[k] for k in ("name", "type_id", "type_version", "icon", "body")}})
                    it = equipment.save_item(actor, i["item_id"], i["name"], i["type_id"], i["type_version"], i["icon"],
                                             i["body"], expected_version=i["base_version"])
                    done(st, version_after=it["version"])
            for b in pkg["bindings"]:
                before = live_point(b["item_id"], b["role"])
                if before != b["point"]:
                    st = intent({"op": "bound", "item_id": b["item_id"], "role": b["role"], "before": before,
                                 "after": b["point"]})
                    equipment.bind(actor, b["item_id"], b["role"], b["point"], note=tag)
                    done(st)
            hyd = pkg.get("hydraulics")
            if hyd and blocks is not None:
                bt, bdg = blocks.topology(), blocks.diagram("installation")
                st = intent({"op": "hydraulics_saved", "before_graph": bt["graph"], "before_diagram": bdg["diagram"],
                             "target_digest": hyd["topology"]["digest"]})
                r = blocks.save_drawing(actor, "installation", hyd["topology"]["graph"], hyd["diagram"]["diagram"],
                                        expected_topology_rev=bt["rev"], expected_diagram_rev=bdg["rev"], note=tag)
                done(st, topology_rev=r["topology"]["rev"], diagram_rev=r["diagram"]["rev"])
                for b in hyd["blocks"]:
                    cur = blocks.latest().get(b["block_id"])
                    st = intent({"op": "block_added", "block_id": b["block_id"], "digest": b["digest"],
                                 "version_before": cur["version"] if cur else None})
                    v = blocks.save_block(actor, b["block_id"], b["name"], b["body"],
                                          expected_version=cur["version"] if cur else None)
                    done(st, version=v["version"])
            for p in pkg["programs"]:
                cur = latest_program(p["program_id"])
                st = intent({"op": "program_added", "program_id": p["program_id"], "digest": p["digest"],
                             "version_before": cur["version"] if cur else None})
                v = programs.save_draft(actor, p["program_id"], p["name"], p["body"])
                st["version"] = v["version"]
                if v["digest"] != p["digest"]:
                    raise ProgramRefused(f"{p['program_id']}: the saved body differs from the exported one")
                programs.record_simulation(p["program_id"], v["version"], {
                    "kind": "AQUACROP_TWIN_EVIDENCE", "program_id": p["program_id"], "version": v["version"],
                    "digest": p["digest"], "passed": True, "accepted_by": pkg["by"], "accepted_utc": pkg["written_utc"],
                    "twin_run_id": pkg["twin_run"], "runs": p.get("runs", []), "note": pkg["note"],
                    "meaning": pkg.get("meaning"), "export": pkg["digest"], "twin_version": p["twin_version"],
                    # the twin's proof context (programs/proof.py): it counts here only while this system matches it
                    "context": ([r.get("context") for r in p.get("runs", []) if r.get("result") == "DONE"] or [None])[-1]})
                done(st, version=v["version"])
            for i in pkg["items"]:
                if i["op"] == "retire":
                    binds = {r: b["point"] for r, b in equipment.bindings(i["item_id"]).items()}
                    st = intent({"op": "item_retired", "item_id": i["item_id"], "before": i["before"], "bindings": binds,
                                 "base_version": i["base_version"]})
                    it = equipment.retire_item(actor, i["item_id"], tag)
                    done(st, version=it["version"])
        except (EquipmentRefused, ProgramRefused, ValueError, OSError, BlockRefused) as e:
            rec["error"] = str(e)
            rec["state"] = "COMPENSATING"            # journaled before the undo, when the journal can be written
            try_save(rec)
            settle(steps)                             # a step that failed midway: was it made or not?
            problems = undo_steps([x for x in steps if x["state"] == "DONE"], actor, f"{tag} failed",
                                  after_each=lambda: try_save(rec))
            problems += [f"{s['op']} {s.get('item_id') or s.get('program_id') or s.get('block_id') or ''}: "
                         f"neither as applied nor as before; check it" for s in steps if s.get("state") == "UNKNOWN"]
            rec["undo_problems"] = problems
            rec["state"] = "FAILED_NOTHING_CHANGED" if not problems else "PARTIAL"
            unsaved = try_save(rec)
            hist.event("TWIN_EXPORT_APPLY_FAILED", {"digest": pkg["digest"], "error": str(e), "problems": problems,
                                                    "journal_unsaved": unsaved}, actor=actor)
            note = "" if unsaved is None else (
                f" The change record could not be written ({unsaved}): this process keeps it, and the next start checks "
                "every step against the real system again.")
            if problems:
                raise HTTPException(409, f"the export failed ({e}) and could not be fully undone: " + "; ".join(problems)
                                    + ". The real system is partly changed; see Changes from the twin." + note)
            raise HTTPException(409, f"nothing was changed: {e}.{note}")
        return steps

    def try_save(rec: dict) -> str | None:
        """Save, and on failure keep the record in memory and note the error on it; None when it was written."""
        try:
            journal.save(rec)
            return None
        except OSError as e:
            rec.setdefault("journal_errors", []).append(f"{_now()}: {e}")
            del rec["journal_errors"][:-5]
            return str(e)

    def fail_point(where: str, step: dict) -> None:
        """Failure injection for the durability tests only (app.state.changes_fail_point)."""
        fn = getattr(app.state, "changes_fail_point", None)
        if fn is not None:
            fn(where, step)

    def found(s: dict) -> str:
        """What the real system shows for one step now: APPLIED (as the step leaves it), REVERTED (as before it, or
        taken back) or UNKNOWN (neither). Fills in the version a made step produced."""
        items = live_items()
        op = s["op"]
        if op in ("item_created", "item_updated"):
            cur = items.get(s["item_id"])
            if (cur is not None and cur["status"] != "RETIRED" and cur["version"] != s.get("base_version")
                    and all(cur[k] == s["target"][k] for k in s["target"])):
                s.setdefault("version_after", cur["version"])
                return "APPLIED"
            if op == "item_created":
                return "REVERTED" if cur is None or cur["status"] == "RETIRED" else "UNKNOWN"
            b = s.get("before") or {}
            return "REVERTED" if cur is not None and cur["status"] != "RETIRED" and all(
                cur[k] == b[k] for k in b) else "UNKNOWN"
        if op == "bound":
            point = live_point(s["item_id"], s["role"])
            return "APPLIED" if point == s["after"] else "REVERTED" if point == s["before"] else "UNKNOWN"
        if op == "program_added":
            mine = [v for v in programs.versions() if v["program_id"] == s["program_id"] and v["digest"] == s["digest"]
                    and (v["version"] or 0) > (s.get("version_before") or 0)
                    and (s.get("version") is None or v["version"] == s["version"])]
            if not mine:
                return "REVERTED"
            s.setdefault("version", mine[-1]["version"])
            return "REVERTED" if mine[-1]["status"] == "RETIRED" else "APPLIED"
        if op == "item_retired":
            cur = items.get(s["item_id"])
            if cur is not None and cur["status"] == "RETIRED" and cur["version"] != s.get("base_version"):
                s.setdefault("version", cur["version"])
                return "APPLIED"
            return "REVERTED" if cur is not None and cur["status"] != "RETIRED" else "UNKNOWN"
        if op == "hydraulics_saved" and blocks is not None:
            from ..topology import definitions as td
            now = blocks.topology()["digest"]
            if now == s["target_digest"]:
                return "APPLIED"
            try:
                before = td.digest(td.parse(s["before_graph"]))
            except Exception:
                return "UNKNOWN"
            return "REVERTED" if now == before else "UNKNOWN"
        if op == "block_added" and blocks is not None:
            cur = blocks.latest().get(s["block_id"])
            if cur is not None and cur["digest"] == s["digest"] and cur["version"] != s.get("version_before"):
                s.setdefault("version", cur["version"])
                return "APPLIED"
            return "REVERTED" if cur is None or cur["version"] == s.get("version_before") else "UNKNOWN"
        return "UNKNOWN"

    def settle(steps: list[dict], states: tuple[str, ...] = ("INTENT",)) -> None:
        """Decide, from what the real system has now, what each step in `states` really is. After a failure only
        steps left at INTENT; at a start also every DONE one, since a compensation whose record could not be
        written may already have taken it back (R07-04)."""
        for s in steps:
            was = s.get("state")
            if was not in states:
                continue
            f = found(s)
            s["state"] = ("DONE" if f == "APPLIED" else "UNKNOWN" if f == "UNKNOWN"
                          else "NOT_DONE" if was == "INTENT" else "UNDONE")
            if s["state"] != was:
                s["settled"] = f"checked against the real system: {was} was {s['state']}"
            elif was == "INTENT":
                s["settled"] = "checked against the real system after an interruption or failure"

    def recover() -> None:
        """At start: a journal left APPLYING, COMPENSATING or UNDOING was interrupted. Record what actually happened,
        checking every step recorded as made, not only those left at INTENT."""
        for rec in journal.all():
            if rec.get("state") in ("APPLYING", "COMPENSATING", "UNDOING"):
                was = rec["state"]
                steps = rec.get("steps", [])
                settle(steps, ("INTENT", "DONE"))
                name = lambda s: f"{s['op']} {s.get('item_id') or s.get('program_id') or s.get('block_id') or ''}".strip()  # noqa: E731
                rec["state"] = "INTERRUPTED"
                rec["interrupted"] = {"during": was, "found_utc": _now(),
                                      "done": [name(s) for s in steps if s.get("state") == "DONE"],
                                      "undone": [name(s) for s in steps if s.get("state") == "UNDONE"],
                                      "unknown": [name(s) for s in steps if s.get("state") == "UNKNOWN"]}
                try_save(rec)
                hist.event("TWIN_EXPORT_INTERRUPTED", {"digest": rec["digest"], "during": was,
                                                       "done": rec["interrupted"]["done"],
                                                       "unknown": rec["interrupted"]["unknown"]}, actor="system")

    def undo_blockers(steps: list[dict]) -> list[str]:
        """Undo only what is still as the export left it."""
        problems = [f"{s['op']} {s.get('item_id') or s.get('program_id') or s.get('block_id') or ''}: its state is unknown "
                    "(neither as applied nor as before); check it on the real system first"
                    for s in steps if s.get("state") == "UNKNOWN"]
        items = live_items()
        for s in steps:
            if s.get("state", "DONE") != "DONE":
                continue
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
            elif s["op"] == "hydraulics_saved" and blocks is not None and blocks.topology()["rev"] != s.get("topology_rev"):
                problems.append("the installation graph changed after the export was applied")
            elif s["op"] == "program_added":
                rt = active_anywhere(s["program_id"], s["version"])
                if rt:
                    problems.append(f"{s['program_id']} v{s['version']} is active ({', '.join(rt)}): deactivate it first")
        return problems

    def undo_steps(steps: list[dict], actor: str, why: str, after_each=None) -> list[str]:
        problems = []
        for s in reversed(steps):
            if s.get("state", "DONE") != "DONE":      # never made: nothing to undo
                continue
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
                elif s["op"] == "hydraulics_saved" and blocks is not None:
                    bt, bdg = blocks.topology(), blocks.diagram("installation")
                    blocks.save_drawing(actor, "installation", s["before_graph"], s["before_diagram"],
                                        expected_topology_rev=bt["rev"], expected_diagram_rev=bdg["rev"], note=why)
                elif s["op"] == "block_added" and blocks is not None:
                    blocks.drop_version(actor, s["block_id"], s["version"], why)
                s["state"] = "UNDONE"
                if after_each is not None:
                    after_each()
            except (EquipmentRefused, ProgramRefused, ValueError, OSError, BlockRefused) as e:
                problems.append(f"{s['op']} {s.get('item_id') or s.get('program_id')}: {e}")
        return problems

    def summary(pkg: dict) -> str:
        n = {"create": 0, "update": 0, "retire": 0}
        for i in pkg["items"]:
            n[i["op"]] += 1
        hyd = pkg.get("hydraulics")
        h = ""
        if hyd:
            h = ("; installation graph changed" if hyd["graph_changed"] else "; installation drawing changed") + \
                (f"; {len(hyd['blocks'])} block version(s)" if hyd["blocks"] else "")
        return (f"{n['create']} created, {n['update']} changed, {n['retire']} retired equipment; "
                f"{len(pkg['bindings'])} channel(s); {len(pkg['programs'])} program version(s){h}")

    @app.post("/api/twin-changes/approve")
    def approve(body: ApproveIn, p: Principal = Depends(require(Perm.OPS_READ))):
        if not can_approve(p):
            raise HTTPException(403, "only administrators and operators with commissioning approve changes from the twin")
        if not apply_lock.acquire(blocking=False):
            raise HTTPException(409, "another approval or undo is being applied now; look again in a moment")
        try:
            return _approve(body, p)
        finally:
            apply_lock.release()

    def _approve(body: ApproveIn, p: Principal):
        prev = journal.get(body.digest)
        if prev and prev.get("state", "APPLIED") != "FAILED_NOTHING_CHANGED":
            raise HTTPException(409, "this export was applied already" if prev.get("state", "APPLIED") == "APPLIED"
                                else f"this export has a {prev['state']} record: resolve it first")
        pkg = package(body.digest)
        c = conflicts(pkg)
        if c:
            raise HTTPException(409, "the real system changed since the twin imported it: " + "; ".join(c)
                                + ". Import in the twin and export again.")
        if any(not x["passed"] for x in pkg["programs"]):
            raise HTTPException(409, "a program in this export has no twin run that ended DONE")
        if any(x.get("failed") for x in pkg["programs"]) and not body.note.strip():
            raise HTTPException(409, "some twin runs of these programs did not end DONE: say why you approve anyway")
        rec = {"digest": pkg["digest"], "by": pkg["by"], "approved_by": p.username, "applied_utc": _now(),
               "note": body.note, "steps": [], "summary": summary(pkg), "twin_run": pkg["twin_run"], "state": "APPLYING"}
        try:
            journal.save(rec, keep=False)             # before anything changes
        except OSError as e:
            raise HTTPException(503, f"nothing was changed: the change record could not be written ({e})") from e
        apply(pkg, p.username, rec)
        rec["state"] = "APPLIED"
        unsaved = try_save(rec)
        hist.event("TWIN_EXPORT_APPLIED", {"digest": pkg["digest"], "summary": rec["summary"], "note": body.note,
                                           "exported_by": pkg["by"], "journal_unsaved": unsaved}, actor=p.username)
        publish()
        out = {k: rec[k] for k in ("digest", "applied_utc", "summary")}
        if unsaved is not None:
            out["warning"] = (f"applied, but its record could not be written ({unsaved}); this process keeps it and the "
                              "next start checks every step against the real system")
        return out

    @app.post("/api/twin-changes/dismiss")
    def dismiss(body: DismissIn, p: Principal = Depends(require(Perm.OPS_READ))):
        """Set aside an export that can no longer be applied (the real system changed since, for example the
        same export sent twice). Nothing on the real system changes; the export stays in the feed directory."""
        if not can_approve(p):
            raise HTTPException(403, "only administrators and operators with commissioning dismiss changes from the twin")
        pkg = package(body.digest)
        c = conflicts(pkg)
        if not c:
            raise HTTPException(409, "this export can still be applied: approve it, or leave it waiting")
        journal.dismiss({"digest": pkg["digest"], "by": pkg["by"], "dismissed_by": p.username,
                         "dismissed_utc": _now(), "note": body.note, "conflicts": c})
        hist.event("TWIN_EXPORT_DISMISSED", {"digest": pkg["digest"], "conflicts": c[:5], "note": body.note},
                   actor=p.username)
        return {"digest": pkg["digest"], "dismissed": True}

    @app.post("/api/twin-changes/undo")
    def undo(body: UndoIn, p: Principal = Depends(require(Perm.OPS_READ))):
        if not can_approve(p):
            raise HTTPException(403, "only administrators and operators with commissioning undo changes from the twin")
        if not apply_lock.acquire(blocking=False):
            raise HTTPException(409, "another approval or undo is being applied now; look again in a moment")
        try:
            return _undo(body, p)
        finally:
            apply_lock.release()

    def _undo(body: UndoIn, p: Principal):
        rec = journal.get(body.digest)
        if rec is None or rec.get("undone_utc") or rec.get("state") == "FAILED_NOTHING_CHANGED":
            raise HTTPException(404, "no applied export to undo with this digest")
        latest = next((r for r in journal.all() if not r.get("undone_utc")
                       and r.get("state") != "FAILED_NOTHING_CHANGED"), None)
        if latest["digest"] != rec["digest"]:
            raise HTTPException(409, "undo the most recent export first")
        blockers = undo_blockers(rec["steps"])
        if blockers:
            raise HTTPException(409, "cannot undo: " + "; ".join(blockers))
        was = rec.get("state", "APPLIED")
        before = json.loads(json.dumps(rec, default=str))
        rec.pop("journal_unsaved", None)
        rec["state"] = "UNDOING"
        try:
            journal.save(rec, keep=False)
        except OSError as e:
            journal.forget_unsaved(rec["digest"])
            if before.get("journal_unsaved"):
                journal.unsaved[rec["digest"]] = {k: v for k, v in before.items() if k != "journal_unsaved"}
            raise HTTPException(503, f"nothing was undone: the change record could not be written ({e})") from e
        problems = undo_steps(rec["steps"], p.username, f"undo of twin export {rec['digest'][:12]}",
                              after_each=lambda: try_save(rec))
        rec.update(undone_utc=_now(), undone_by=p.username, undo_note=body.note, undo_problems=problems,
                   state="UNDONE" if not problems else "PARTIAL", undone_from=was)
        try_save(rec)
        hist.event("TWIN_EXPORT_UNDONE", {"digest": rec["digest"], "problems": problems, "note": body.note},
                   actor=p.username)
        publish()
        return {"digest": rec["digest"], "problems": problems}

    recover()
