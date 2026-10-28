"""HTTP routes of Blocks: the hydraulic graph, its drawings and the blocks (0.7.0). Registered by the live app and
by the twin app, each over its own database.

Who may do what (server-side): read: operators and administrators (OPS_READ); save graph, drawing and block
drafts, validate a block: operators (EQUIPMENT_EDIT); retire a block: administrators (EQUIPMENT_ACTIVATE). Every
save names the revision it was edited from and is refused when another session saved in between. Nothing here
reaches a module, activates anything or gives run authority: a validated block is a checked structure.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .. import diagram as dg
from ..blocks import definitions as bd
from ..blocks.store import BlockRefused, BlockStore
from ..security.auth import Perm, Principal
from ..topology import definitions as td
from ..topology.validation import paths_from


class _B(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TopologyIn(_B):
    graph: dict
    expected_rev: int = Field(ge=0)
    note: str = Field(default="", max_length=200)


class DiagramIn(_B):
    diagram: dict
    expected_rev: int = Field(ge=0)


class DrawingIn(_B):
    graph: dict
    diagram: dict
    expected_topology_rev: int = Field(ge=0)
    expected_diagram_rev: int = Field(ge=0)
    note: str = Field(default="", max_length=200)


class BlockIn(_B):
    block_id: str = Field(max_length=48)
    name: str = Field(max_length=80)
    body: dict
    expected_version: int | None = Field(default=None, ge=1)


class BlockRefIn(_B):
    block_id: str = Field(max_length=48)
    version: int = Field(ge=1)


class TemplateIn(_B):
    template: str = Field(max_length=40)
    supply: str = Field(max_length=48)
    destination: str = Field(max_length=48)
    travel_s: float | None = Field(default=None, gt=0, le=600)


def register(app, require, store: BlockStore, *, dependents=lambda block_id: [], legacy_layout=None) -> None:
    def refused(e: BlockRefused):
        return JSONResponse({"detail": str(e), "issues": e.issues}, status_code=409)

    def names() -> dict[str, str]:
        items = store.items()
        return {n.id: (items.get(n.item or "", {}) or {}).get("name") or n.id for n in store.graph().nodes}

    @app.get("/api/topology")
    def topology_view(p: Principal = Depends(require(Perm.OPS_READ))):
        cur = store.topology()
        return cur | {"issues": store.topology_issues(), "symbols": td.symbols_public(),
                      "items": store.items(), "history": store.history(10),
                      "meaning": "Checks prove structure only: not that the real pipes match the drawing."}

    @app.post("/api/topology")
    def topology_save(body: TopologyIn, p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        try:
            return store.save_topology(p.username, body.graph, expected_rev=body.expected_rev, note=body.note)
        except BlockRefused as e:
            return refused(e)

    @app.get("/api/topology/paths")
    def topology_paths(node: str, p: Principal = Depends(require(Perm.OPS_READ))):
        return {"node": node, "paths": paths_from(store.graph(), node)}

    @app.get("/api/diagram/{view}")
    def diagram_view(view: str, p: Principal = Depends(require(Perm.OPS_READ))):
        d = store.diagram(view)
        t = store.graph()
        return d | {"issues": dg.route_issues(dg.parse(d["diagram"]), t), "topology_rev": d["topology_rev"],
                    "current_topology_rev": store.topology()["rev"]}

    @app.post("/api/diagram/{view}")
    def diagram_save(view: str, body: DiagramIn, p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        try:
            return store.save_diagram(p.username, view, body.diagram, expected_rev=body.expected_rev)
        except BlockRefused as e:
            return refused(e)

    @app.post("/api/drawing/{view}")
    def drawing_save(view: str, body: DrawingIn, p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        try:
            return store.save_drawing(p.username, view, body.graph, body.diagram,
                                      expected_topology_rev=body.expected_topology_rev,
                                      expected_diagram_rev=body.expected_diagram_rev, note=body.note)
        except BlockRefused as e:
            return refused(e)

    @app.get("/api/topology/migration")
    def migration_preview(p: Principal = Depends(require(Perm.OPS_READ))):
        """What the old drawing says, as a reviewed proposal (nothing is saved by looking at it)."""
        if legacy_layout is None:
            raise HTTPException(404, "no older drawing here")
        from ..topology.migrate import propose
        lay, where = legacy_layout()
        items = store.items()
        return propose(lay, {k: v.get("kind") for k, v in items.items()}, {k: v.get("name") for k, v in items.items()}) \
            | {"from": where}

    @app.get("/api/blocks")
    def blocks_view(p: Principal = Depends(require(Perm.OPS_READ))):
        latest = store.latest()
        t = store.graph()
        nm = names()
        share: dict[str, list[str]] = {}
        for b in latest.values():
            if b["status"] != "RETIRED":
                for m in b["body"]["members"]:
                    share.setdefault(m, []).append(b["block_id"])
        out = []
        for bid, b in sorted(latest.items()):
            body = bd.parse(b["body"])
            path = bd.process_path(body, t)
            out.append(b | {"stale": store.stale(b), "path": path,
                            "shared": {m: [x for x in share.get(m, []) if x != bid] for m in body.members
                                       if len(share.get(m, [])) > 1},
                            "names": {m: nm.get(m, m) for m in body.members}})
        return {"blocks": out, "templates": bd.TEMPLATES, "topology_rev": store.topology()["rev"]}

    @app.get("/api/blocks/{block_id}/{version}/check")
    def block_check(block_id: str, version: int, p: Principal = Depends(require(Perm.OPS_READ))):
        try:
            return {"issues": store.check(block_id, version)}
        except BlockRefused as e:
            return refused(e)

    @app.post("/api/blocks")
    def block_save(body: BlockIn, p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        try:
            return store.save_block(p.username, body.block_id, body.name, body.body, expected_version=body.expected_version)
        except BlockRefused as e:
            return refused(e)

    @app.post("/api/blocks/validate")
    def block_validate(body: BlockRefIn, p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        try:
            return store.validate(p.username, body.block_id, body.version)
        except BlockRefused as e:
            return refused(e)

    @app.post("/api/blocks/retire")
    def block_retire(body: BlockRefIn, p: Principal = Depends(require(Perm.EQUIPMENT_ACTIVATE))):
        try:
            return store.retire(p.username, body.block_id, dependents(body.block_id))
        except BlockRefused as e:
            return refused(e)

    @app.post("/api/blocks/template-plan")
    def block_template(body: TemplateIn, p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        """An example plan along the explicit path, for the editor to show and the operator to edit."""
        t = store.graph()
        paths = [x for x in paths_from(t, body.supply) if x["to"] == body.destination]
        if not paths:
            raise HTTPException(409, "no connected path between those two: connect the pipes first")
        path = sorted(paths, key=lambda x: (len(x["nodes"]), x["edges"]))[0]
        kinds = {n.id: n.kind for n in t.nodes}
        plan = bd.template_plan(body.template, path["nodes"], kinds, body.travel_s)
        meters = [n for n in path["nodes"] if kinds[n] == "meter"]
        return {"path": path, "members": sorted({body.supply, body.destination, *path["nodes"]}),
                "meter": meters[0] if meters else None, "plan": plan.model_dump(mode="json"),
                "note": "An example from the template: check every step against the real installation."}
