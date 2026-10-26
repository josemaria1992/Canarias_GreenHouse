"""Persistence of programs (drafts, immutable versions, per-runtime activations) and of the engine's state
(occurrences, conflicts, installation mode, recovery), in the runtime's own database (schema 5).

Lifecycle of a program version: DRAFT (editable) -> VALIDATED (immutable; validation recorded) -> activated per
runtime. A VALIDATED version that fails a later revalidation becomes INVALID (still immutable) and is deactivated
everywhere. A twin activation needs only validation. A live activation needs the same version's (same digest)
simulation evidence: a twin run that passed, recorded against it. Editing a VALIDATED version creates the next
DRAFT. Running operations keep the version they started with. Importing makes drafts only.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from .definitions import ProgramBody, digest, errors_text, parse


class ProgramRefused(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class MemoryEngineStore:
    """For tests and headless checks."""

    def __init__(self):
        self.mode: list[dict] = []
        self.occ: dict[str, dict] = {}
        self.conflicts: dict[str, dict] = {}
        self.recovery: list[dict] = []

    def mode_load(self):
        return self.mode[-1] if self.mode else None

    def mode_save(self, mode, phase, actor, reason, extra):
        self.mode.append({"mode": mode, "phase": phase, "actor": actor, "reason": reason} | extra)

    def occurrence_claim(self, key, program_id, version, scheduled_utc, state, doc):
        if key in self.occ:
            return False
        self.occ[key] = {"program_id": program_id, "version": version, "state": state, "doc": doc,
                         "scheduled_utc": scheduled_utc}
        return True

    def occurrence_update(self, key, state, *, outcome=None, doc=None):
        o = self.occ.setdefault(key, {"doc": {}})
        o["state"], o["outcome"] = state, outcome
        o["doc"] = (o.get("doc") or {}) | (doc or {})

    def interrupt_running(self):
        hit = [k for k, o in self.occ.items() if o["state"] in ("RUNNING", "STARTING", "STOPPING")]
        for k in hit:
            self.occ[k]["state"] = "INTERRUPTED"
        return hit

    def conflict_open(self, cid, doc):
        self.conflicts[cid] = dict(doc)

    def conflict_close(self, cid, resolution, actor):
        self.conflicts[cid] |= {"state": "CLOSED", "resolution": resolution, "actor": actor}

    def recovery_save(self, state, actor, doc):
        self.recovery.append({"state": state, "actor": actor} | doc)

    def usage_load(self):
        return {k: dict(v) for k, v in getattr(self, "usage", {}).items()}

    def usage_save(self, item_id, doc):
        self.__dict__.setdefault("usage", {})[item_id] = dict(doc)


class SqlEngineStore:
    def __init__(self, hist, runtime_id: str, process_session_id: str, *, key_prefix: str | None = None):
        """`key_prefix` namespaces occurrence keys (the twin: one namespace per simulation run, so a reset to the
        same scenario date runs its windows again, while a restart within a run still deduplicates)."""
        self.h, self.rt, self.sess = hist, runtime_id, process_session_id
        self.prefix = f"{key_prefix}:" if key_prefix else ""
        self._usage_rt = f"{runtime_id}:{key_prefix}" if key_prefix else runtime_id   # a twin reset is a new scenario

    def mode_load(self):
        rows = self.h.query("SELECT mode, phase, actor, utc, reason FROM installation_mode WHERE runtime_id=? "
                            "ORDER BY seq DESC LIMIT 1", (self.rt,))
        return dict(rows[0]) if rows else None

    def mode_save(self, mode, phase, actor, reason, extra):
        row = (self.rt, mode, phase, actor, _now(), reason, json.dumps(extra, default=str))
        self.h.submit(lambda c: c.execute("INSERT INTO installation_mode(runtime_id, mode, phase, actor, utc, reason, json)"
                                          " VALUES(?,?,?,?,?,?,?)", row), wait=True)

    def occurrence_claim(self, key, program_id, version, scheduled_utc, state, doc):
        key = self.prefix + key
        row = (key, self.rt, program_id, version, scheduled_utc.isoformat() if scheduled_utc else None, state, _now(),
               json.dumps(doc, default=str))

        def tx(c):
            cur = c.execute("INSERT OR IGNORE INTO occurrences(occurrence_key, runtime_id, program_id, version, "
                            "scheduled_utc, state, created_utc, json) VALUES(?,?,?,?,?,?,?,?)", row)
            return cur.rowcount == 1
        return bool(self.h.submit(tx, wait=True))

    def occurrence_update(self, key, state, *, outcome=None, doc=None):
        self._update(self.prefix + key, state, outcome=outcome, doc=doc)

    def _update(self, key, state, *, outcome=None, doc=None):
        now = _now()

        def tx(c):
            r = c.execute("SELECT json, started_utc FROM occurrences WHERE occurrence_key=?", (key,)).fetchone()
            if r is None:
                return
            merged = json.loads(r[0] or "{}") | (doc or {})
            started = r[1] or (now if state == "RUNNING" else None)
            ended = now if state not in ("RUNNING", "STARTING", "WAITING_CONFLICT") else None
            c.execute("UPDATE occurrences SET state=?, outcome=COALESCE(?, outcome), json=?, started_utc=?, "
                      "ended_utc=COALESCE(?, ended_utc) WHERE occurrence_key=?",
                      (state, outcome, json.dumps(merged, default=str), started, ended, key))
        self.h.submit(tx, wait=True)

    def interrupt_running(self):
        rows = self.h.query("SELECT occurrence_key FROM occurrences WHERE runtime_id=? AND state IN "
                            "('RUNNING','STARTING','STOPPING','WAITING_CONFLICT')", (self.rt,))
        keys = [r["occurrence_key"] for r in rows]
        for k in keys:                                # stored keys, already namespaced
            self._update(k, "INTERRUPTED", outcome="controller restarted: not resumed")
        return keys

    def conflict_open(self, cid, doc):
        row = (cid, self.rt, "OPEN", _now(), json.dumps(doc, default=str))
        self.h.submit(lambda c: c.execute("INSERT INTO conflicts(conflict_id, runtime_id, state, opened_utc, json) "
                                          "VALUES(?,?,?,?,?)", row), wait=True)

    def conflict_close(self, cid, resolution, actor):
        self.h.submit(lambda c: c.execute("UPDATE conflicts SET state='CLOSED', resolved_utc=?, resolution=?, actor=? "
                                          "WHERE conflict_id=?", (_now(), resolution, actor, cid)), wait=True)

    def recovery_save(self, state, actor, doc):
        row = (self.rt, self.sess, state, actor, _now(), json.dumps(doc, default=str))
        self.h.submit(lambda c: c.execute("INSERT INTO recovery(runtime_id, process_session_id, state, actor, utc, json) "
                                          "VALUES(?,?,?,?,?,?)", row), wait=True)

    def usage_load(self) -> dict:
        """Starts and the last verified OFF per item (schema 6), so a restart is not a limit reset."""
        rows = self.h.query("SELECT item_id, json FROM equipment_usage WHERE runtime_id=?", (self._usage_rt,))
        return {r["item_id"]: json.loads(r["json"]) for r in rows}

    def usage_save(self, item_id: str, doc: dict) -> None:
        row = (self._usage_rt, item_id, _now(), json.dumps(doc))
        self.h.submit(lambda c: c.execute("INSERT INTO equipment_usage(runtime_id, item_id, updated_utc, json) "
                                          "VALUES(?,?,?,?) ON CONFLICT(runtime_id, item_id) DO UPDATE SET "
                                          "updated_utc=excluded.updated_utc, json=excluded.json", row), wait=True)

    def occurrences(self, limit: int = 100) -> list[dict]:
        rows = self.h.query("SELECT * FROM occurrences WHERE runtime_id=? ORDER BY created_utc DESC LIMIT ?",
                            (self.rt, limit))
        return [dict(r) | {"doc": json.loads(r["json"] or "{}")} for r in rows]


class ProgramStore:
    def __init__(self, hist):
        self.h = hist
        self._lock = threading.RLock()

    def versions(self) -> list[dict]:
        rows = self.h.query("SELECT * FROM programs ORDER BY program_id, version")
        return [dict(r) | {"body": json.loads(r["json"]), "validation": json.loads(r["validation_json"] or "null"),
                           "simulation": json.loads(r["simulation_json"] or "null")} for r in rows]

    def get(self, program_id: str, version: int) -> dict:
        hit = [v for v in self.versions() if v["program_id"] == program_id and v["version"] == version]
        if not hit:
            raise ProgramRefused(f"program {program_id} v{version} does not exist")
        return hit[0]

    def save_draft(self, actor: str, program_id: str, name: str, body: dict, *, version: int | None = None) -> dict:
        import re
        if not re.fullmatch(r"[a-z0-9][a-z0-9_\-]{0,47}", program_id or ""):
            raise ProgramRefused("program id: 1-48 lower-case letters, digits, - or _")
        name = (name or "").strip()
        if not 1 <= len(name) <= 80:
            raise ProgramRefused("give the program a name of 1-80 characters")
        try:
            canon = parse(body).model_dump(mode="json")
        except Exception as e:
            from pydantic import ValidationError
            raise ProgramRefused("; ".join(errors_text(e)) if isinstance(e, ValidationError) else str(e))
        with self._lock:
            have = [v for v in self.versions() if v["program_id"] == program_id]
            if version is not None:
                hit = [v for v in have if v["version"] == version]
                if not hit or hit[0]["status"] != "DRAFT":
                    raise ProgramRefused(f"{program_id} v{version} is not a draft: edits make a new version")
            else:
                if any(v["status"] == "DRAFT" for v in have):
                    raise ProgramRefused(f"{program_id} already has a draft; edit that one")
                version = (have[-1]["version"] + 1) if have else 1
            now = _now()
            row = (program_id, version, "DRAFT", name, digest(canon), now, actor, now, json.dumps(canon, sort_keys=True))
            self.h.submit(lambda c: c.execute(
                "INSERT INTO programs(program_id, version, status, name, digest, created_utc, created_by, updated_utc, json)"
                " VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(program_id, version) DO UPDATE SET name=excluded.name, "
                "digest=excluded.digest, updated_utc=excluded.updated_utc, json=excluded.json, validation_json=NULL "
                "WHERE programs.status='DRAFT'", row), wait=True)
            self.h.event("PROGRAM_DRAFT_SAVED", {"program": f"{program_id}@{version}", "digest": digest(canon)}, actor=actor)
            return self.get(program_id, version)

    def mark_validated(self, actor: str, program_id: str, version: int, issues: list[str], context: dict) -> dict:
        """A DRAFT with issues stays DRAFT. A version that was VALIDATED is immutable: with issues it becomes INVALID
        (never editable again; a fix is the next version) and every activation of it ends, so no runtime keeps
        running a body that no longer validates."""
        with self._lock:
            v = self.get(program_id, version)
            if v["status"] not in ("DRAFT", "VALIDATED", "INVALID"):
                raise ProgramRefused(f"{program_id} v{version} is {v['status']}")
            doc = {"utc": _now(), "actor": actor, "issues": issues, "context": context, "digest": v["digest"]}
            status = "VALIDATED" if not issues else ("DRAFT" if v["status"] == "DRAFT" else "INVALID")
            self.h.submit(lambda c: c.execute("UPDATE programs SET status=?, validation_json=?, updated_utc=? WHERE "
                                              "program_id=? AND version=?",
                                              (status, json.dumps(doc), _now(), program_id, version)), wait=True)
            self.h.event("PROGRAM_VALIDATED" if not issues else "PROGRAM_VALIDATION_FAILED",
                         {"program": f"{program_id}@{version}", "issues": issues}, actor=actor)
            if status == "INVALID":
                self._deactivate_everywhere(actor, program_id, version, "no longer validates: " + "; ".join(issues[:3]))
            return self.get(program_id, version)

    def _deactivate_everywhere(self, actor: str, program_id: str, version: int, why: str) -> None:
        rows = self.h.query("SELECT a.runtime_id, a.version, a.action FROM program_activations a JOIN "
                            "(SELECT runtime_id, max(seq) AS seq FROM program_activations WHERE program_id=? "
                            "GROUP BY runtime_id) m ON a.seq=m.seq", (program_id,))
        for r in rows:
            if r["action"] == "ACTIVATED" and r["version"] == version:
                row = (r["runtime_id"], program_id, version, "DEACTIVATED", actor, _now(), json.dumps({"why": why}))
                self.h.submit(lambda c, row=row: c.execute(
                    "INSERT INTO program_activations(runtime_id, program_id, version, action, actor, utc, json) "
                    "VALUES(?,?,?,?,?,?,?)", row), wait=True)
                self.h.event("PROGRAM_DEACTIVATED", {"program": program_id, "runtime": r["runtime_id"], "why": why},
                             actor=actor)

    def record_simulation(self, program_id: str, version: int, evidence: dict) -> dict:
        v = self.get(program_id, version)
        if evidence.get("digest") != v["digest"]:
            raise ProgramRefused("the twin ran a different version of this program")
        self.h.submit(lambda c: c.execute("UPDATE programs SET simulation_json=? WHERE program_id=? AND version=?",
                                          (json.dumps(evidence), program_id, version)), wait=True)
        self.h.event("PROGRAM_SIMULATION_RECORDED", {"program": f"{program_id}@{version}",
                                                     "passed": evidence.get("passed")}, actor="twin")
        return self.get(program_id, version)

    # ------------------------------------------------------------------ activation per runtime
    def active(self, runtime_id: str) -> dict[str, tuple[int, ProgramBody]]:
        rows = self.h.query("SELECT a.program_id, a.version, a.action FROM program_activations a JOIN "
                            "(SELECT program_id, max(seq) AS seq FROM program_activations WHERE runtime_id=? "
                            "GROUP BY program_id) m ON a.seq=m.seq", (runtime_id,))
        out = {}
        for r in rows:
            if r["action"] != "ACTIVATED":
                continue
            v = self.get(r["program_id"], r["version"])
            if v["status"] != "VALIDATED":           # only an immutable, validated body ever runs
                continue
            out[r["program_id"]] = (r["version"], parse(v["body"]))
        return out

    def activate(self, actor: str, runtime_id: str, program_id: str, version: int, *, require_simulation: bool,
                 overlaps_accepted: bool, overlaps: list[str], context: str | None = None) -> dict:
        """`context`: the proof context digest of this version on this system now (programs/proof.py); with
        `require_simulation` the accepted twin result must have been taken in exactly that context."""
        from .proof import refusal
        with self._lock:
            v = self.get(program_id, version)
            if v["status"] != "VALIDATED":
                raise ProgramRefused(f"{program_id} v{version} is not validated")
            if require_simulation and (why := refusal(v["simulation"], v["digest"], context)) is not None:
                raise ProgramRefused(why)
            if overlaps and not overlaps_accepted:
                raise ProgramRefused("foreseeable overlaps with active programs: " + "; ".join(overlaps[:5]) +
                                     " — accept them to activate (each one raises a conflict you resolve)")
            row = (runtime_id, program_id, version, "ACTIVATED", actor, _now(),
                   json.dumps({"digest": v["digest"], "overlaps": overlaps, "context": context}))
            self.h.submit(lambda c: c.execute("INSERT INTO program_activations(runtime_id, program_id, version, action, "
                                              "actor, utc, json) VALUES(?,?,?,?,?,?,?)", row), wait=True)
            self.h.event("PROGRAM_ACTIVATED", {"program": f"{program_id}@{version}", "runtime": runtime_id}, actor=actor)
            return v

    def deactivate(self, actor: str, runtime_id: str, program_id: str) -> None:
        act = self.active(runtime_id)
        if program_id not in act:
            raise ProgramRefused(f"{program_id} is not active")
        row = (runtime_id, program_id, act[program_id][0], "DEACTIVATED", actor, _now(), "{}")
        self.h.submit(lambda c: c.execute("INSERT INTO program_activations(runtime_id, program_id, version, action, "
                                          "actor, utc, json) VALUES(?,?,?,?,?,?,?)", row), wait=True)
        self.h.event("PROGRAM_DEACTIVATED", {"program": program_id, "runtime": runtime_id}, actor=actor)

    def dependents(self, item_id: str) -> list[str]:
        from .definitions import actuators, sensors
        out = set()
        for v in self.versions():
            if v["status"] == "RETIRED":
                continue
            try:
                b = parse(v["body"])
            except Exception:
                continue
            if item_id in actuators(b) or item_id in {i for i, _ in sensors(b)}:
                out.add(v["program_id"])
        return sorted(out)
