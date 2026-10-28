"""HTTP routes of the equipment pages (types, items, channel bindings). Registered by `create_app`.

Who may do what (server-side, on every request):
* read everything: any signed-in user;
* save type drafts, create and edit items, preview upgrades: operators (EQUIPMENT_EDIT);
* activate or retire a type, retire an item: administrators (EQUIPMENT_ACTIVATE);
* choose and verify a channel for a role: commissioning users (IO_ASSIGN), as on the I/O board;
* change an item's public ID (0.7.1, `aquacrop.ids`): operators (EQUIPMENT_EDIT), unique and under a revision check;
* send and review requests for features the software cannot do yet (owner, 28 Oct 2026): operators and administrators
  (EQUIPMENT_EDIT). A request is an audited record; approving it changes no type, item or behaviour.
Nothing here sends anything to a module.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from ..equipment.definitions import ICONS, TEMPLATES
from ..topology.definitions import symbols_public
from ..equipment.store import EquipmentRefused
from ..security.auth import Perm, Principal


class _B(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TypeDraftIn(_B):
    type_id: str = Field(max_length=48)
    name: str = Field(max_length=80)
    body: dict
    version: int | None = Field(default=None, ge=1)


class TypeRefIn(_B):
    type_id: str = Field(max_length=48)
    version: int = Field(ge=1)


class RetireIn(_B):
    reason: str = Field(min_length=3, max_length=200)


class ItemIn(_B):
    item_id: str = Field(max_length=48)              # an existing item's key or ID; for a new item, the ID typed
    name: str = Field(max_length=80)
    type_id: str = Field(max_length=48)
    type_version: int | None = Field(default=None, ge=1)
    icon: str = Field(max_length=24)
    body: dict = {}
    expected_version: int | None = Field(default=None, ge=1)


class RenameIn(_B):
    public_id: str = Field(max_length=48)
    expected_rev: int = Field(ge=0)


class UpgradeIn(_B):
    to_version: int = Field(ge=1)
    expected_version: int = Field(ge=1)


class BindIn(_B):
    item_id: str = Field(max_length=48)
    role: str = Field(max_length=48)
    point: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=200)


class BindVerifyIn(_B):
    item_id: str = Field(max_length=48)
    role: str = Field(max_length=48)
    observation: str = Field(max_length=300)
    reference: float | None = None
    tolerance: float | None = Field(default=None, gt=0)
    cite_assignment: bool = False


class ImportIn(_B):
    doc: dict


class TypeRequestIn(_B):
    need: str = Field(min_length=3, max_length=2000)
    what: Literal["output", "other"] = "other"
    type_name: str = Field(default="", max_length=80)
    type_id: str | None = Field(default=None, max_length=48)


class TypeRequestReviewIn(_B):
    request_id: int = Field(ge=1)
    decision: Literal["APPROVED", "REJECTED"]
    note: str = Field(default="", max_length=500)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


REQUEST_MEANING = ("Requests for features the software cannot do yet are recorded on this bench and shown here to "
                   "operators and administrators, who review them and approve or reject them. Approving records that "
                   "the need is accepted for a software change: it changes no equipment type, item, program or output. "
                   "Nothing is emailed or sent elsewhere.")


class TypeAvailIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type_id: str = Field(pattern=r"^[a-z0-9_]{1,48}$")


def register(app, require, store, samples, edges) -> None:
    def refused(e: EquipmentRefused):
        raise HTTPException(409, str(e))

    @app.get("/api/equipment")
    def equipment_view(p: Principal = Depends(require(Perm.OPS_READ))):
        items = store.items()
        return {"types": store.types(), "items": items, "icons": list(ICONS),
                "templates": {k: v["name"] for k, v in TEMPLATES.items()},
                # UX-07: the guided type form starts from these and previews the diagram symbols it will be drawn with
                "template_bodies": {k: {"name": v["name"], "body": v["body"]} for k, v in TEMPLATES.items()},
                "symbols": symbols_public(),
                "availability": store.availability(),
                "bindings": list(store.bindings().values()),
                "readiness": {i["item_id"]: store.readiness(i["item_id"]) for i in items if i["status"] == "ACTIVE"}}

    @app.post("/api/equipment/types/draft")
    def type_draft(body: TypeDraftIn, p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        try:
            return store.save_type_draft(p.username, body.type_id, body.name, body.body, version=body.version)
        except EquipmentRefused as e:
            refused(e)

    @app.post("/api/equipment/types/seed")
    def type_seed(p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        return {"created": store.seed_templates(p.username)}

    # owner, 28 Oct 2026: a need the type form cannot meet goes to the operators, who review and approve or reject it.
    # Kept as audited events (no new table): the request and each decision.
    def type_requests() -> list[dict]:
        rows = store.h.query("SELECT id, utc, kind, actor, payload_json FROM events WHERE kind IN "
                             "('EQUIPMENT_TYPE_REQUEST', 'EQUIPMENT_TYPE_REQUEST_REVIEWED') ORDER BY id")
        out: dict[int, dict] = {}
        for r in rows:
            d = json.loads(r["payload_json"] or "{}")
            if r["kind"] == "EQUIPMENT_TYPE_REQUEST":
                out[r["id"]] = {"request_id": r["id"], "utc": r["utc"], "by": r["actor"], "need": d.get("need", ""),
                                "what": d.get("what", "other"), "type_name": d.get("type_name", ""),
                                "type_id": d.get("type_id"), "status": "OPEN", "reviewer": None, "review_utc": None,
                                "note": ""}
            elif d.get("request_id") in out and out[d["request_id"]]["status"] == "OPEN":
                out[d["request_id"]] |= {"status": d.get("decision"), "reviewer": r["actor"], "review_utc": r["utc"],
                                         "note": d.get("note", "")}
        return sorted(out.values(), key=lambda x: x["request_id"], reverse=True)

    @app.get("/api/equipment/type-requests")
    def type_requests_view(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
                           p: Principal = Depends(require(Perm.OPS_READ))):
        """R913-04: every request waiting for review, always (never cut off by newer ones); the reviewed history newest
        first, a page at a time."""
        allr = type_requests()
        waiting = [r for r in allr if r["status"] == "OPEN"]
        done = [r for r in allr if r["status"] != "OPEN"]
        page = done[offset:offset + limit]
        return {"open": waiting, "reviewed": page, "reviewed_total": len(done), "offset": offset, "limit": limit,
                "requests": waiting + page, "can_review": p.can(Perm.EQUIPMENT_EDIT), "meaning": REQUEST_MEANING}

    @app.post("/api/equipment/type-requests")
    def type_request(body: TypeRequestIn, p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        row = body.model_dump()

        def tx(c):
            return c.execute("INSERT INTO events(utc,kind,actor,session_id,run_id,sim_s,payload_json) VALUES(?,?,?,?,?,?,?)",
                             (_now(), "EQUIPMENT_TYPE_REQUEST", p.username, store.h.session_id, None, None,
                              json.dumps(row))).lastrowid
        rid = store.h.submit(tx, wait=True)
        return {"ok": True, "request_id": rid, "status": "OPEN",
                "next": f"Sent to the operators as request {rid}. They review it on Equipment types and approve or reject "
                        "it; nothing about the equipment changes."}

    @app.post("/api/equipment/type-requests/review")
    def type_request_review(body: TypeRequestReviewIn, p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        if body.decision == "REJECTED" and not body.note.strip():
            raise HTTPException(422, "say why the request is rejected")

        def tx(c):                                     # one decision per request, checked inside the write
            req = c.execute("SELECT id FROM events WHERE id=? AND kind='EQUIPMENT_TYPE_REQUEST'", (body.request_id,)).fetchone()
            if req is None:
                return "missing"
            for (pj,) in c.execute("SELECT payload_json FROM events WHERE kind='EQUIPMENT_TYPE_REQUEST_REVIEWED'"):
                if json.loads(pj or "{}").get("request_id") == body.request_id:
                    return "decided"
            c.execute("INSERT INTO events(utc,kind,actor,session_id,run_id,sim_s,payload_json) VALUES(?,?,?,?,?,?,?)",
                      (_now(), "EQUIPMENT_TYPE_REQUEST_REVIEWED", p.username, store.h.session_id, None, None,
                       json.dumps(body.model_dump())))
            return "ok"
        res = store.h.submit(tx, wait=True)
        if res == "missing":
            raise HTTPException(404, "no such request")
        if res == "decided":
            raise HTTPException(409, f"request {body.request_id} was already reviewed")
        return {"ok": True, "request_id": body.request_id, "status": body.decision}

    @app.post("/api/equipment/types/make-available")
    def type_make_available(body: TypeAvailIn, p: Principal = Depends(require(Perm.EQUIPMENT_ACTIVATE))):
        """UX-05: add a built-in type from its template (when missing) and activate it, in one deliberate step."""
        try:
            return store.make_available(p.username, body.type_id)
        except EquipmentRefused as e:
            raise HTTPException(409, str(e))

    @app.post("/api/equipment/types/activate")
    def type_activate(body: TypeRefIn, p: Principal = Depends(require(Perm.EQUIPMENT_ACTIVATE))):
        try:
            return store.activate_type(p.username, body.type_id, body.version)
        except EquipmentRefused as e:
            refused(e)

    @app.post("/api/equipment/types/{type_id}/retire")
    def type_retire(type_id: str, body: RetireIn, p: Principal = Depends(require(Perm.EQUIPMENT_ACTIVATE))):
        try:
            store.retire_type(p.username, type_id, body.reason)
            return {"retired": type_id}
        except EquipmentRefused as e:
            refused(e)

    @app.post("/api/equipment/items")
    def item_save(body: ItemIn, p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        try:
            return store.save_item(p.username, body.item_id, body.name, body.type_id, body.type_version, body.icon,
                                   body.body, expected_version=body.expected_version)
        except EquipmentRefused as e:
            refused(e)

    @app.post("/api/equipment/items/{item_id}/id")
    def item_rename(item_id: str, body: RenameIn, p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        """Change the public ID. The item's key and every reference to it stay; nothing runs or stops."""
        try:
            return store.rename_item(p.username, store.resolve(item_id), body.public_id, body.expected_rev)
        except EquipmentRefused as e:
            refused(e)

    @app.get("/api/ids/suggest")
    def id_suggest(name: str = Query("", max_length=80), p: Principal = Depends(require(Perm.OPS_READ))):
        from ..ids import id_from_name
        return {"name": name, "id": id_from_name(name)}

    @app.get("/api/equipment/items/{item_id}/upgrade")
    def item_upgrade_preview(item_id: str, to_version: int | None = Query(None, ge=1),
                             p: Principal = Depends(require(Perm.OPS_READ))):
        try:
            return store.upgrade_preview(item_id, to_version)
        except EquipmentRefused as e:
            refused(e)

    @app.post("/api/equipment/items/{item_id}/upgrade")
    def item_upgrade(item_id: str, body: UpgradeIn, p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        try:
            return store.upgrade_item(p.username, item_id, body.to_version, body.expected_version)
        except EquipmentRefused as e:
            refused(e)

    @app.post("/api/equipment/items/{item_id}/retire")
    def item_retire(item_id: str, body: RetireIn, p: Principal = Depends(require(Perm.EQUIPMENT_ACTIVATE))):
        try:
            return store.retire_item(p.username, item_id, body.reason)
        except EquipmentRefused as e:
            refused(e)

    @app.post("/api/equipment/bind")
    def bind(body: BindIn, p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        # 0.7.0 (ADR-0023): any operator proposes a channel (PROPOSED: no output right, not ready for a physical run);
        # replacing or clearing a VERIFIED channel is wiring work and needs commissioning
        cur = store.bindings(body.item_id).get(body.role)
        if not p.can(Perm.IO_ASSIGN) and cur is not None and cur["status"] == "VERIFIED":
            raise HTTPException(403, "this channel is verified at the bench: changing it needs commissioning")
        try:
            return store.bind(p.username, body.item_id, body.role, body.point or None, body.note)
        except EquipmentRefused as e:
            refused(e)

    @app.post("/api/equipment/bind/verify")
    def bind_verify(body: BindVerifyIn, p: Principal = Depends(require(Perm.IO_ASSIGN))):
        try:
            return store.verify(p.username, body.item_id, body.role, observation=body.observation, samples=samples(),
                                edges=edges, reference=body.reference, tolerance=body.tolerance,
                                cite_assignment=body.cite_assignment)
        except EquipmentRefused as e:
            refused(e)

    @app.get("/api/equipment/export")
    def export(p: Principal = Depends(require(Perm.OPS_READ))):
        return store.export()

    @app.post("/api/equipment/import")
    def import_(body: ImportIn, p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        try:
            return store.import_(p.username, body.doc)
        except EquipmentRefused as e:
            refused(e)
