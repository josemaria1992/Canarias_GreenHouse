"""Persistence and lifecycle of equipment types, items and channel bindings (schema 5).

Types: DRAFT (editable) -> ACTIVE (immutable) -> SUPERSEDED when a newer version is activated, or
RETIRED. Editing an ACTIVE type creates the next DRAFT version. Activating a new version changes no
item: each item stays on the type version it was made from until a user upgrades it, after a preview
of what the upgrade adds, removes and invalidates.

Items: every save is a new version; the newest version is the item. RETIRED keeps the history and the
bindings' records; retirement is refused while a program uses the item (`dependents`).

Bindings: append-only per (item, role), like the I/O assignments of ADR-0017. One channel carries at
most one role of one item. VERIFIED needs the same server-side check as an assignment (DO ON and OFF
confirmed by readback, DI both ways, AI a fresh reading within tolerance) plus the user's observation,
or an earlier VERIFIED I/O assignment of the same channel, which is cited, not copied. A changed item
version or a type upgrade that alters a role sends its binding back to PROPOSED.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from typing import Callable

from pydantic import ValidationError

from ..control.assignments import AssignmentRefused, EdgeWatch
from .definitions import (ICONS, ID_RE, TEMPLATES, ItemBody, Purpose, RoleDef, TypeBody, channel_issues, digest,
                          errors_text, item_issues)

_ID = re.compile(ID_RE)


def _role_digest(r: RoleDef, ib: ItemBody) -> str:
    sc = ib.scaling.get(r.role)
    return digest({"role": r.model_dump(mode="json"), "scaling": sc.model_dump(mode="json") if sc else None})


class EquipmentRefused(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class EquipmentStore:
    def __init__(self, hist, cfg, assignments, *, dependents: Callable[[str], list[str]] | None = None):
        self.h, self.cfg, self.assignments = hist, cfg, assignments
        self.dependents = dependents or (lambda item_id: [])
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ helpers
    def _log(self, actor: str, kind: str, ref: str, action: str, payload: dict) -> None:
        row = (_now(), actor, kind, ref, action, json.dumps(payload, sort_keys=True))
        self.h.submit(lambda c: c.execute("INSERT INTO equipment_log(utc, actor, kind, ref, action, json) "
                                          "VALUES(?,?,?,?,?,?)", row), wait=True)
        self.h.event(f"EQUIPMENT_{kind}_{action}", {"ref": ref} | payload, actor=actor)

    @staticmethod
    def _check_id(x: str, what: str) -> None:
        if not isinstance(x, str) or not _ID.match(x):
            raise EquipmentRefused(f"{what} must be 1-48 lower-case letters, digits, - or _")

    # ------------------------------------------------------------------ types
    def types(self) -> list[dict]:
        rows = self.h.query("SELECT type_id, version, status, name, digest, created_utc, created_by, updated_utc, json "
                            "FROM equipment_types ORDER BY type_id, version")
        return [dict(r) | {"body": json.loads(r["json"])} for r in rows]

    def type_version(self, type_id: str, version: int) -> dict:
        rows = self.h.query("SELECT type_id, version, status, name, digest, json FROM equipment_types "
                            "WHERE type_id=? AND version=?", (type_id, version))
        if not rows:
            raise EquipmentRefused(f"type {type_id} v{version} does not exist")
        r = rows[0]
        return dict(r) | {"body": json.loads(r["json"])}

    def _type_body(self, type_id: str, version: int) -> TypeBody:
        return TypeBody.model_validate(self.type_version(type_id, version)["body"])

    def _active_type(self, type_id: str) -> dict | None:
        rows = self.h.query("SELECT version FROM equipment_types WHERE type_id=? AND status='ACTIVE'", (type_id,))
        return self.type_version(type_id, rows[0]["version"]) if rows else None

    def save_type_draft(self, actor: str, type_id: str, name: str, body: dict, *, version: int | None = None) -> dict:
        self._check_id(type_id, "type id")
        name = (name or "").strip()
        if not 1 <= len(name) <= 80:
            raise EquipmentRefused("give the type a name of 1-80 characters")
        try:
            parsed = TypeBody.model_validate(body)
        except ValidationError as e:
            raise EquipmentRefused("; ".join(errors_text(e)))
        canon = parsed.model_dump(mode="json")
        with self._lock:
            existing = self.h.query("SELECT version, status FROM equipment_types WHERE type_id=? ORDER BY version",
                                    (type_id,))
            if version is not None:
                hit = [r for r in existing if r["version"] == version]
                if not hit:
                    raise EquipmentRefused(f"type {type_id} v{version} does not exist")
                if hit[0]["status"] != "DRAFT":
                    raise EquipmentRefused(f"type {type_id} v{version} is {hit[0]['status']}: edits make a new version")
            else:
                if any(r["status"] == "DRAFT" for r in existing):
                    raise EquipmentRefused(f"type {type_id} already has a draft; edit that one")
                version = (existing[-1]["version"] + 1) if existing else 1
            now = _now()
            row = (type_id, version, "DRAFT", name, digest(canon), now, actor, now, json.dumps(canon, sort_keys=True))
            self.h.submit(lambda c: c.execute(
                "INSERT INTO equipment_types(type_id, version, status, name, digest, created_utc, created_by, "
                "updated_utc, json) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(type_id, version) DO UPDATE SET "
                "name=excluded.name, digest=excluded.digest, updated_utc=excluded.updated_utc, json=excluded.json "
                "WHERE equipment_types.status='DRAFT'", row), wait=True)
            self._log(actor, "TYPE", f"{type_id}@{version}", "DRAFT_SAVED", {"name": name, "digest": digest(canon)})
            return self.type_version(type_id, version)

    def activate_type(self, actor: str, type_id: str, version: int) -> dict:
        with self._lock:
            t = self.type_version(type_id, version)
            if t["status"] != "DRAFT":
                raise EquipmentRefused(f"type {type_id} v{version} is {t['status']}; only a draft is activated")
            TypeBody.model_validate(t["body"])
            prev = self._active_type(type_id)
            now = _now()

            def tx(c):
                if prev:
                    c.execute("UPDATE equipment_types SET status='SUPERSEDED', updated_utc=? WHERE type_id=? AND "
                              "version=?", (now, type_id, prev["version"]))
                c.execute("UPDATE equipment_types SET status='ACTIVE', updated_utc=? WHERE type_id=? AND version=?",
                          (now, type_id, version))
            self.h.submit(tx, wait=True)
            affected = [i["item_id"] for i in self.items() if i["type_id"] == type_id and i["status"] == "ACTIVE"]
            self._log(actor, "TYPE", f"{type_id}@{version}", "ACTIVATED",
                      {"superseded": prev["version"] if prev else None, "items_on_older_versions": affected})
            return self.type_version(type_id, version)

    def retire_type(self, actor: str, type_id: str, reason: str) -> None:
        with self._lock:
            users = [i["item_id"] for i in self.items() if i["type_id"] == type_id and i["status"] == "ACTIVE"]
            if users:
                raise EquipmentRefused(f"type {type_id} is used by {', '.join(users)}: retire or move them first")
            now = _now()
            self.h.submit(lambda c: c.execute("UPDATE equipment_types SET status='RETIRED', updated_utc=? WHERE "
                                              "type_id=? AND status IN ('ACTIVE','DRAFT')", (now, type_id)), wait=True)
            self._log(actor, "TYPE", type_id, "RETIRED", {"reason": reason})

    def seed_templates(self, actor: str = "system") -> list[str]:
        """Add each built-in template as a DRAFT type when no type of that id exists yet (never overwrites)."""
        made = []
        have = {t["type_id"] for t in self.types()}
        for tid, t in TEMPLATES.items():
            if tid not in have:
                self.save_type_draft(actor, tid, t["name"], t["body"])
                made.append(tid)
        return made

    # ------------------------------------------------------------------ items
    def items(self, *, include_retired: bool = True) -> list[dict]:
        rows = self.h.query(
            "SELECT i.* FROM equipment_items i JOIN (SELECT item_id, max(version) AS v FROM equipment_items "
            "GROUP BY item_id) m ON i.item_id=m.item_id AND i.version=m.v ORDER BY i.name")
        out = [dict(r) | {"body": json.loads(r["json"])} for r in rows]
        return [x for x in out if include_retired or x["status"] != "RETIRED"]

    def item(self, item_id: str) -> dict:
        hit = [i for i in self.items() if i["item_id"] == item_id]
        if not hit:
            raise EquipmentRefused(f"equipment {item_id} does not exist")
        return hit[0]

    def save_item(self, actor: str, item_id: str, name: str, type_id: str, type_version: int | None, icon: str,
                  body: dict, *, expected_version: int | None = None) -> dict:
        self._check_id(item_id, "equipment id")
        name = (name or "").strip()
        if not 1 <= len(name) <= 80:
            raise EquipmentRefused("give the equipment a name of 1-80 characters")
        if icon not in ICONS:
            raise EquipmentRefused(f"icon {icon!r} is not a built-in icon")
        with self._lock:
            if type_version is None:
                act = self._active_type(type_id)
                if act is None:
                    raise EquipmentRefused(f"type {type_id} has no active version; activate it first")
                type_version = act["version"]
            t = self.type_version(type_id, type_version)
            if t["status"] not in ("ACTIVE", "SUPERSEDED"):
                raise EquipmentRefused(f"type {type_id} v{type_version} is {t['status']}: items use activated types")
            tb = TypeBody.model_validate(t["body"])
            try:
                ib = ItemBody.model_validate(body)
            except ValidationError as e:
                raise EquipmentRefused("; ".join(errors_text(e)))
            issues = item_issues(tb, ib, icon)
            if issues:
                raise EquipmentRefused("; ".join(issues))
            for other in self.items(include_retired=False):
                if other["item_id"] != item_id and other["name"].casefold() == name.casefold():
                    raise EquipmentRefused(f"another equipment item is already called {name}")
            prev = self.h.query("SELECT max(version) AS v, status FROM equipment_items WHERE item_id=?", (item_id,))
            cur = prev[0]["v"] if prev and prev[0]["v"] is not None else None
            if expected_version != cur:
                raise EquipmentRefused(f"equipment {item_id} changed meanwhile (now v{cur}); reload and edit again"
                                       if cur else f"equipment {item_id} does not exist yet")
            if cur is not None and self.item(item_id)["status"] == "RETIRED":
                raise EquipmentRefused(f"equipment {item_id} is retired")
            version = (cur or 0) + 1
            canon = ib.model_dump(mode="json")
            now = _now()
            row = (item_id, version, "ACTIVE", type_id, type_version, name, icon, digest(canon), now, actor, now,
                   json.dumps(canon, sort_keys=True))
            self.h.submit(lambda c: c.execute(
                "INSERT INTO equipment_items(item_id, version, status, type_id, type_version, name, icon, digest, "
                "created_utc, created_by, updated_utc, json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", row), wait=True)
            self._log(actor, "ITEM", f"{item_id}@{version}", "SAVED",
                      {"name": name, "type": f"{type_id}@{type_version}", "icon": icon})
            if cur is not None:
                self._requalify(actor, item_id, tb, ib, reason=f"equipment changed to v{version}")
            return self.item(item_id)

    def upgrade_preview(self, item_id: str, to_version: int | None = None) -> dict:
        it = self.item(item_id)
        act = self._active_type(it["type_id"]) if to_version is None else self.type_version(it["type_id"], to_version)
        if act is None:
            raise EquipmentRefused(f"type {it['type_id']} has no active version")
        old = self._type_body(it["type_id"], it["type_version"])
        new = TypeBody.model_validate(act["body"])
        o, n = {r.role: r for r in old.roles}, {r.role: r for r in new.roles}
        bound = self.bindings(item_id)
        changed = [r for r in o if r in n and o[r] != n[r]]
        issues = item_issues(new, ItemBody.model_validate(it["body"]), it["icon"])
        return {"item_id": item_id, "from": it["type_version"], "to": act["version"],
                "roles_added": sorted(set(n) - set(o)), "roles_removed": sorted(set(o) - set(n)),
                "roles_changed": sorted(changed),
                "bindings_cleared": sorted(r for r in set(o) - set(n) if r in bound),
                "bindings_back_to_proposed": sorted(r for r in changed if r in bound),
                "item_issues": issues, "programs": self.dependents(item_id)}

    def upgrade_item(self, actor: str, item_id: str, to_version: int, expected_version: int) -> dict:
        prev = self.upgrade_preview(item_id, to_version)
        if prev["item_issues"]:
            raise EquipmentRefused("; ".join(prev["item_issues"]))
        it = self.item(item_id)
        return self.save_item(actor, item_id, it["name"], it["type_id"], to_version, it["icon"], it["body"],
                              expected_version=expected_version)

    def retire_item(self, actor: str, item_id: str, reason: str) -> dict:
        with self._lock:
            it = self.item(item_id)
            users = self.dependents(item_id)
            if users:
                raise EquipmentRefused(f"{it['name']} is used by {', '.join(users)}: change or retire them first")
            for role, b in self.bindings(item_id).items():
                self._bind_write(actor, item_id, role, None, "CLEARED", f"equipment retired: {reason}", {})
            now = _now()
            self.h.submit(lambda c: c.execute("UPDATE equipment_items SET status='RETIRED', updated_utc=? WHERE "
                                              "item_id=? AND version=?", (now, item_id, it["version"])), wait=True)
            self._log(actor, "ITEM", item_id, "RETIRED", {"reason": reason})
            return self.item(item_id)

    # ------------------------------------------------------------------ bindings
    def bindings(self, item_id: str | None = None) -> dict:
        rows = self.h.query(
            "SELECT b.* FROM channel_bindings b JOIN (SELECT item_id, role, max(seq) AS seq FROM channel_bindings "
            "WHERE config_id=? GROUP BY item_id, role) m ON b.seq=m.seq", (self.cfg.config_id,))
        cur = {}
        for r in rows:
            if r["status"] == "CLEARED":
                continue
            v = {"item_id": r["item_id"], "role": r["role"], "point": r["point"], "status": r["status"],
                 "actor": r["actor"], "utc": r["utc"], "note": r["note"],
                 "evidence": json.loads(r["evidence_json"] or "{}")}
            cur[(r["item_id"], r["role"])] = v
        if item_id is not None:
            return {role: v for (iid, role), v in cur.items() if iid == item_id}
        return cur

    def by_point(self) -> dict[str, dict]:
        return {v["point"]: v for v in self.bindings().values()}

    def _bind_write(self, actor, item_id, role, point, status, note, evidence) -> dict:
        row = (item_id, role, point, status, actor, _now(), note, json.dumps(evidence, sort_keys=True),
               self.cfg.config_id, self.cfg.config_version)
        self.h.submit(lambda c: c.execute(
            "INSERT INTO channel_bindings(item_id, role, point, status, actor, utc, note, evidence_json, config_id, "
            "config_version) VALUES(?,?,?,?,?,?,?,?,?,?)", row), wait=True)
        self._log(actor, "BINDING", f"{item_id}.{role}", status, {"point": point, "note": note, "evidence": evidence})
        return {"item_id": item_id, "role": role, "point": point, "status": status, "evidence": evidence}

    def _role(self, item_id: str, role: str) -> tuple[dict, TypeBody, RoleDef]:
        it = self.item(item_id)
        if it["status"] == "RETIRED":
            raise EquipmentRefused(f"{it['name']} is retired")
        tb = self._type_body(it["type_id"], it["type_version"])
        r = next((x for x in tb.roles if x.role == role), None)
        if r is None:
            raise EquipmentRefused(f"{it['name']} has no role {role}")
        return it, tb, r

    def bind(self, actor: str, item_id: str, role: str, point: str | None, note: str | None = None) -> dict:
        with self._lock:
            it, tb, r = self._role(item_id, role)
            cur = self.bindings(item_id).get(role)
            if point is None:
                if cur is None:
                    raise EquipmentRefused(f"{it['name']} {r.label} has no channel to clear")
                return self._bind_write(actor, item_id, role, None, "CLEARED", note, {})
            ib = ItemBody.model_validate(it["body"])
            issues = channel_issues(self.cfg, r, point, ib.scaling.get(role))
            if issues:
                raise EquipmentRefused("; ".join(issues))
            other = self.by_point().get(point)
            if other and (other["item_id"], other["role"]) != (item_id, role):
                raise EquipmentRefused(f"{point} already carries {other['item_id']}.{other['role']}: clear it there first")
            if cur and cur["point"] == point:
                raise EquipmentRefused(f"{it['name']} {r.label} is already on {point}")
            return self._bind_write(actor, item_id, role, point, "PROPOSED", note, {})

    def verify(self, actor: str, item_id: str, role: str, *, observation: str, samples: dict, edges: EdgeWatch,
               reference: float | None = None, tolerance: float | None = None, cite_assignment: bool = False,
               now: datetime | None = None) -> dict:
        """VERIFIED after the same check as an I/O assignment, or by citing this channel's VERIFIED assignment."""
        observation = (observation or "").strip()
        if len(observation) < 3:
            raise EquipmentRefused("describe what you saw the device do (at least 3 characters)")
        with self._lock:
            it, tb, r = self._role(item_id, role)
            cur = self.bindings(item_id).get(role)
            if cur is None:
                raise EquipmentRefused(f"{it['name']} {r.label} has no channel: choose one first")
            point = cur["point"]
            if cite_assignment:
                a = self.assignments.current().get(point)
                if not a or a["status"] != "VERIFIED":
                    raise EquipmentRefused(f"{point} has no VERIFIED I/O assignment to cite")
                evidence = {"check": "CITED_IO_ASSIGNMENT", "assignment_utc": a["utc"], "assignment_by": a["actor"],
                            "assignment_signal": a["signal"], "assignment_evidence": a["evidence"]}
            else:
                start = datetime.fromisoformat(cur["utc"])
                try:
                    if r.direction.value == "OUTPUT":
                        evidence = self.assignments._do_evidence(actor, point, start)
                    elif r.signal.value == "DIGITAL":
                        evidence = self.assignments._di_evidence(point, start, edges)
                    else:
                        evidence = self.assignments._ai_evidence(point, samples.get(point),
                                                                 now or datetime.now(timezone.utc), reference, tolerance)
                except AssignmentRefused as e:
                    raise EquipmentRefused(str(e))
            evidence["observation"] = observation
            evidence["proof"] = "the user's observation of the device; readback and readings show the module only"
            evidence["role_digest"] = _role_digest(r, ItemBody.model_validate(it["body"]))
            evidence["item_version"] = it["version"]
            return self._bind_write(actor, item_id, role, point, "VERIFIED", cur["note"], evidence)

    def _requalify(self, actor: str, item_id: str, tb: TypeBody, ib: ItemBody, reason: str) -> None:
        roles = {r.role: r for r in tb.roles}
        for role, b in self.bindings(item_id).items():
            r = roles.get(role)
            if r is None:
                self._bind_write(actor, item_id, role, None, "CLEARED", f"{reason}: role removed", {})
            elif channel_issues(self.cfg, r, b["point"], ib.scaling.get(role)):
                self._bind_write(actor, item_id, role, None, "CLEARED", f"{reason}: channel no longer fits", {})
            elif b["status"] == "VERIFIED" and b["evidence"].get("role_digest") != _role_digest(r, ib):
                self._bind_write(actor, item_id, role, b["point"], "PROPOSED", f"{reason}: verify again", {})

    # ------------------------------------------------------------------ views
    def readiness(self, item_id: str) -> dict:
        """What an item still lacks before a program may use it (the dashboard's checklist)."""
        it = self.item(item_id)
        tb = self._type_body(it["type_id"], it["type_version"])
        b = self.bindings(item_id)
        ib = ItemBody.model_validate(it["body"])
        missing = [r.label for r in tb.roles if r.required and r.role not in b]
        unverified = [r.label for r in tb.roles if r.role in b and b[r.role]["status"] != "VERIFIED"]
        unscaled = [r.label for r in tb.roles if r.purpose == Purpose.MEASUREMENT and r.role in b
                    and r.role not in ib.scaling]
        return {"item_id": item_id, "missing_required": missing, "unverified": unverified, "unscaled": unscaled,
                "ready": not (missing or unverified or unscaled) and it["status"] == "ACTIVE"}

    def export(self) -> dict:
        return {"kind": "AQUACROP_EQUIPMENT_EXPORT", "config_id": self.cfg.config_id,
                "exported_utc": _now(),
                "types": [{"type_id": t["type_id"], "version": t["version"], "status": t["status"], "name": t["name"],
                           "body": t["body"]} for t in self.types()],
                "items": [{"item_id": i["item_id"], "version": i["version"], "status": i["status"], "name": i["name"],
                           "type_id": i["type_id"], "type_version": i["type_version"], "icon": i["icon"],
                           "body": i["body"]} for i in self.items()],
                "bindings": [{"item_id": v["item_id"], "role": v["role"], "point": v["point"], "status": v["status"]}
                             for v in self.bindings().values()],
                "note": "Import makes DRAFT types and items without channels; bindings are not imported."}

    def import_(self, actor: str, doc: dict) -> dict:
        """Import types as new DRAFT versions and items as new items without bindings. Nothing activates."""
        if doc.get("kind") != "AQUACROP_EQUIPMENT_EXPORT":
            raise EquipmentRefused("not an AquaCrop equipment export")
        made = {"types": [], "skipped_items": []}
        for t in doc.get("types", []):
            if t.get("status") in ("ACTIVE", "SUPERSEDED", "DRAFT"):
                try:
                    has_draft = any(x["type_id"] == t["type_id"] and x["status"] == "DRAFT" for x in self.types())
                    if not has_draft:
                        self.save_type_draft(actor, t["type_id"], t["name"], t["body"])
                        made["types"].append(t["type_id"])
                except EquipmentRefused as e:
                    raise EquipmentRefused(f"type {t.get('type_id')}: {e}")
        made["skipped_items"] = [i.get("item_id") for i in doc.get("items", [])]
        made["note"] = ("Items are listed, not created: activate the imported types, then create the items so their "
                        "channels are chosen and verified on this installation.")
        self._log(actor, "IMPORT", "equipment", "IMPORTED", made)
        return made
