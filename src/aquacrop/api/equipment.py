"""HTTP routes of the equipment pages (types, items, channel bindings). Registered by `create_app`.

Who may do what (server-side, on every request):
* read everything: any signed-in user;
* save type drafts, create and edit items, preview upgrades: operators (EQUIPMENT_EDIT);
* activate or retire a type, retire an item: administrators (EQUIPMENT_ACTIVATE);
* choose and verify a channel for a role: commissioning users (IO_ASSIGN), as on the I/O board;
* change an item's public ID (0.7.1, `aquacrop.ids`): operators (EQUIPMENT_EDIT), unique and under a revision check.
Nothing here sends anything to a module.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from ..equipment.definitions import ICONS, TEMPLATES
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
