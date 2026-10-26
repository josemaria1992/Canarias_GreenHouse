"""Persistence of the hydraulic graph, its drawings and the blocks (schema 6), in the runtime's own database.

* The graph has numbered revisions per workspace ("main"). A save names the revision it was edited from
  (`expected_rev`); another session's newer save is a conflict, never overwritten. Each revision keeps its
  semantic digest, so evidence can name the exact graph it was tested on.
* A drawing (one per view: the installation, or one block) has its own revisions and names the graph revision it
  draws. Saving a drawing never changes the graph; a drawing whose routes fail the strict checks is refused.
* A block version is DRAFT (editable) or VALIDATED (immutable; a change makes the next version); RETIRED keeps its
  history. Validation records the graph revision and digest it was checked against.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone

from pydantic import ValidationError

from .. import diagram as dg
from ..topology import definitions as td
from ..topology.validation import issues as topo_issues
from . import definitions as bd

WORKSPACE = "main"


class BlockRefused(Exception):
    def __init__(self, msg: str, issues: list[dict] | None = None):
        super().__init__(msg)
        self.issues = issues or []


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _errors(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(x) for x in err.get('loc', ()))}: {err.get('msg', '').removeprefix('Value error, ')}"
                     for err in e.errors())


class BlockStore:
    def __init__(self, hist, items=None):
        """`items()` -> {item_id: {"kind": hydraulic kind or None, "name": ...}} of the active equipment."""
        self.h = hist
        self.items = items or (lambda: {})
        self._lock = threading.RLock()
        self._current: td.Topology | None = None   # the latest graph, cached: this process is its only writer

    # ------------------------------------------------------------------ the graph
    def topology(self, rev: int | None = None) -> dict:
        if rev is None:
            rows = self.h.query("SELECT * FROM topology_revisions WHERE workspace=? ORDER BY rev DESC LIMIT 1", (WORKSPACE,))
        else:
            rows = self.h.query("SELECT * FROM topology_revisions WHERE workspace=? AND rev=?", (WORKSPACE, rev))
        if not rows:
            return {"rev": 0, "digest": td.digest(td.Topology()), "graph": td.Topology().model_dump(mode="json"),
                    "actor": None, "utc": None, "note": "empty"}
        r = rows[0]
        return {"rev": r["rev"], "digest": r["digest"], "graph": json.loads(r["json"]), "actor": r["actor"],
                "utc": r["utc"], "note": r["note"]}

    def graph(self, rev: int | None = None) -> td.Topology:
        if rev is not None:
            return td.parse(self.topology(rev)["graph"])
        if self._current is None:
            self._current = td.parse(self.topology()["graph"])
        return self._current

    def save_topology(self, actor: str, doc: dict, *, expected_rev: int, note: str = "") -> dict:
        try:
            t = td.parse(doc)
        except ValidationError as e:
            raise BlockRefused(_errors(e))
        with self._lock:
            cur = self.topology()
            if cur["rev"] != expected_rev:
                raise BlockRefused(f"the installation graph changed meanwhile (revision {cur['rev']}, you edited "
                                   f"{expected_rev}); reload and make your change again")
            d = td.digest(t)
            if d == cur["digest"]:
                return cur | {"unchanged": True}
            rev = cur["rev"] + 1
            row = (WORKSPACE, rev, "DRAFT", d, actor, _now(), note[:200], json.dumps(td.canonical(t), sort_keys=True))
            self.h.submit(lambda c: c.execute("INSERT INTO topology_revisions(workspace, rev, status, digest, actor, utc, "
                                              "note, json) VALUES(?,?,?,?,?,?,?,?)", row), wait=True)
            self._current = None
            self.h.event("TOPOLOGY_SAVED", {"rev": rev, "digest": d, "note": note[:200]}, actor=actor)
            return self.topology(rev) | {"issues": topo_issues(t, self.items())}

    def topology_issues(self, rev: int | None = None) -> list[dict]:
        return topo_issues(self.graph(rev), self.items())

    def history(self, limit: int = 50) -> list[dict]:
        rows = self.h.query("SELECT rev, digest, actor, utc, note FROM topology_revisions WHERE workspace=? "
                            "ORDER BY rev DESC LIMIT ?", (WORKSPACE, limit))
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ drawings
    def diagram(self, view: str = "installation", rev: int | None = None) -> dict:
        if rev is None:
            rows = self.h.query("SELECT * FROM diagram_revisions WHERE view=? ORDER BY rev DESC LIMIT 1", (view,))
        else:
            rows = self.h.query("SELECT * FROM diagram_revisions WHERE view=? AND rev=?", (view, rev))
        if not rows:
            return {"view": view, "rev": 0, "topology_rev": None, "digest": None,
                    "diagram": dg.Diagram(view=view).model_dump(mode="json")}
        r = rows[0]
        return {"view": view, "rev": r["rev"], "topology_rev": r["topology_rev"], "digest": r["digest"],
                "diagram": json.loads(r["json"]), "actor": r["actor"], "utc": r["utc"]}

    def save_diagram(self, actor: str, view: str, doc: dict, *, expected_rev: int) -> dict:
        if not re.fullmatch(td.ID, view):
            raise BlockRefused("a view id: lower-case letters, digits, - or _")
        try:
            d = dg.parse(dict(doc, view=view))
        except ValidationError as e:
            raise BlockRefused(_errors(e))
        with self._lock:
            cur = self.diagram(view)
            if cur["rev"] != expected_rev:
                raise BlockRefused(f"this drawing changed meanwhile (revision {cur['rev']}, you edited {expected_rev}); "
                                   "reload it")
            topo = self.topology()
            problems = [i for i in dg.route_issues(d, td.parse(topo["graph"])) if i["severity"] == "ERROR"]
            if problems:
                raise BlockRefused("the drawing has pipes that cannot be saved: " +
                                   "; ".join(i["message"] for i in problems[:5]), problems)
            pdig = dg.presentation_digest(d)
            if pdig == cur["digest"] and cur["topology_rev"] == topo["rev"]:
                return cur | {"unchanged": True}
            rev = cur["rev"] + 1
            row = (view, rev, topo["rev"], pdig, actor, _now(), json.dumps(dg.canonical(d), sort_keys=True))
            self.h.submit(lambda c: c.execute("INSERT INTO diagram_revisions(view, rev, topology_rev, digest, actor, utc, "
                                              "json) VALUES(?,?,?,?,?,?,?)", row), wait=True)
            self.h.event("DIAGRAM_SAVED", {"view": view, "rev": rev, "topology_rev": topo["rev"]}, actor=actor)
            return self.diagram(view, rev) | {"warnings": [i for i in dg.route_issues(d, td.parse(topo["graph"]))
                                                           if i["severity"] == "WARNING"]}

    def save_drawing(self, actor: str, view: str, graph: dict, diagram: dict, *, expected_topology_rev: int,
                     expected_diagram_rev: int, note: str = "") -> dict:
        """The editor's Save: the graph and its drawing together. Both are checked before either is written, so a
        refused drawing never leaves a half-saved graph. A drawing-only edit (moves, reroutes) keeps the graph
        revision and digest unchanged."""
        try:
            t = td.parse(graph)
            d = dg.parse(dict(diagram, view=view))
        except ValidationError as e:
            raise BlockRefused(_errors(e))
        with self._lock:
            cur_t, cur_d = self.topology(), self.diagram(view)
            if cur_t["rev"] != expected_topology_rev or cur_d["rev"] != expected_diagram_rev:
                raise BlockRefused(f"someone saved meanwhile (graph revision {cur_t['rev']}, drawing revision "
                                   f"{cur_d['rev']}); reload and make your change again")
            problems = [i for i in dg.route_issues(d, t) if i["severity"] == "ERROR"]
            if problems:
                raise BlockRefused("the drawing has pipes that cannot be saved: " +
                                   "; ".join(i["message"] for i in problems[:5]), problems)
            semantic = td.digest(t) != cur_t["digest"]
            t_out = self.save_topology(actor, graph, expected_rev=expected_topology_rev, note=note) if semantic else cur_t
            d_out = self.save_diagram(actor, view, diagram, expected_rev=expected_diagram_rev)
            return {"topology": {k: t_out[k] for k in ("rev", "digest")}, "diagram": {k: d_out[k] for k in ("rev", "digest")},
                    "semantic_change": semantic, "issues": topo_issues(t, self.items()),
                    "warnings": d_out.get("warnings", [])}

    # ------------------------------------------------------------------ exchange with the twin
    def export(self) -> dict:
        """For the live snapshot: the current graph, the installation drawing and every block version."""
        return {"topology": self.topology(), "diagram": self.diagram("installation"),
                "blocks": [{k: b[k] for k in ("block_id", "version", "status", "name", "digest", "body", "validation")}
                           for b in self.blocks()]}

    def import_snapshot(self, doc: dict | None, actor: str = "live-snapshot") -> dict:
        """In the twin: follow the real system's graph, drawing and blocks while the twin has not changed them
        itself. A twin edit (a revision by anyone else than the snapshot) is kept; the difference stays visible."""
        if not doc:
            return {"topology": False, "diagram": False, "blocks": 0}
        out = {"topology": False, "diagram": False, "blocks": 0}
        with self._lock:
            cur = self.topology()
            live = doc.get("topology") or {}
            if live.get("digest") and live["digest"] != cur["digest"] and cur["actor"] in (None, actor):
                self.save_topology(actor, live["graph"], expected_rev=cur["rev"], note="from the real system")
                out["topology"] = True
            d = doc.get("diagram") or {}
            cd = self.diagram("installation")
            if d.get("digest") and d["digest"] != cd["digest"] and cd.get("actor") in (None, actor):
                try:
                    self.save_diagram(actor, "installation", d["diagram"], expected_rev=cd["rev"])
                    out["diagram"] = True
                except BlockRefused:
                    pass                                # a drawing that no longer fits: the twin redraws it
            have = {(b["block_id"], b["version"]): b for b in self.blocks()}
            for b in doc.get("blocks", []):
                key = (b["block_id"], b["version"])
                if key in have:
                    continue
                row = (b["block_id"], b["version"], b["status"] if b["status"] in ("VALIDATED", "RETIRED") else "DRAFT",
                       b["name"], b["digest"], (b.get("validation") or {}).get("topology_rev"), _now(), actor, _now(),
                       json.dumps(b["body"], sort_keys=True), json.dumps(b.get("validation")) if b.get("validation") else None)
                self.h.submit(lambda c, row=row: c.execute(
                    "INSERT INTO blocks(block_id, version, status, name, digest, topology_rev, created_utc, created_by, "
                    "updated_utc, json, validation_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)", row), wait=True)
                out["blocks"] += 1
        return out

    # ------------------------------------------------------------------ blocks
    def blocks(self) -> list[dict]:
        rows = self.h.query("SELECT * FROM blocks ORDER BY block_id, version")
        return [dict(r) | {"body": json.loads(r["json"]), "validation": json.loads(r["validation_json"] or "null")}
                for r in rows]

    def latest(self) -> dict[str, dict]:
        out = {}
        for b in self.blocks():
            out[b["block_id"]] = b
        return out

    def get(self, block_id: str, version: int) -> dict:
        hit = [b for b in self.blocks() if b["block_id"] == block_id and b["version"] == version]
        if not hit:
            raise BlockRefused(f"block {block_id} v{version} does not exist")
        return hit[0]

    def save_block(self, actor: str, block_id: str, name: str, body: dict, *, expected_version: int | None) -> dict:
        """A new block, or the next DRAFT of one, or an edit of its DRAFT. `expected_version`: the version the
        editor started from (None for a new block)."""
        if not re.fullmatch(td.ID, block_id or ""):
            raise BlockRefused("block id: 1-48 lower-case letters, digits, - or _")
        name = (name or "").strip()
        if not 1 <= len(name) <= 80:
            raise BlockRefused("give the block a name of 1-80 characters")
        try:
            b = bd.parse(body)
        except ValidationError as e:
            raise BlockRefused(_errors(e))
        canon = b.model_dump(mode="json")
        with self._lock:
            have = [x for x in self.blocks() if x["block_id"] == block_id]
            last = have[-1] if have else None
            if (last["version"] if last else None) != expected_version:
                raise BlockRefused(f"{block_id} changed meanwhile (now v{last['version'] if last else 'none'}); reload it")
            for other in self.latest().values():
                if other["block_id"] != block_id and other["status"] != "RETIRED" and other["name"].casefold() == name.casefold():
                    raise BlockRefused(f"another block is already called {name}")
            now = _now()
            if last is not None and last["status"] == "DRAFT":
                version = last["version"]
                row = (name, bd.digest(b), now, json.dumps(canon, sort_keys=True), block_id, version)
                self.h.submit(lambda c: c.execute("UPDATE blocks SET name=?, digest=?, updated_utc=?, json=?, "
                                                  "validation_json=NULL WHERE block_id=? AND version=? AND status='DRAFT'",
                                                  row), wait=True)
            else:
                version = (last["version"] + 1) if last else 1
                row = (block_id, version, "DRAFT", name, bd.digest(b), None, now, actor, now,
                       json.dumps(canon, sort_keys=True))
                self.h.submit(lambda c: c.execute("INSERT INTO blocks(block_id, version, status, name, digest, topology_rev, "
                                                  "created_utc, created_by, updated_utc, json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                                                  row), wait=True)
            self.h.event("BLOCK_DRAFT_SAVED", {"block": f"{block_id}@{version}", "digest": bd.digest(b)}, actor=actor)
            return self.get(block_id, version) | {"issues": self.check(block_id, version)}

    def check(self, block_id: str, version: int) -> list[dict]:
        b = self.get(block_id, version)
        t = self.graph()
        names = {n.id: (self.items().get(n.item or "", {}) or {}).get("name") or n.id for n in t.nodes}
        out = bd.check(bd.parse(b["body"]), t, names)
        tissues = topo_issues(t, self.items())
        members = set(b["body"]["members"])
        for i in tissues:                              # graph problems that touch this block's equipment
            if i["severity"] == "ERROR" and (i["object"] in members or i["object"].split(".")[0] in members):
                out.append(i)
        return out

    def validate(self, actor: str, block_id: str, version: int) -> dict:
        with self._lock:
            b = self.get(block_id, version)
            if b["status"] != "DRAFT":
                raise BlockRefused(f"{block_id} v{version} is {b['status']}: a change makes the next version")
            found = self.check(block_id, version)
            errs = [i for i in found if i["severity"] == "ERROR"]
            if errs:
                raise BlockRefused(f"{len(errs)} problem(s) to correct before {b['name']} validates", found)
            topo = self.topology()
            doc = {"utc": _now(), "actor": actor, "topology_rev": topo["rev"], "topology_digest": topo["digest"],
                   "warnings": [i for i in found if i["severity"] == "WARNING"],
                   "meaning": "structure checked against the drawn graph; not proof that the real pipes match it"}
            self.h.submit(lambda c: c.execute("UPDATE blocks SET status='VALIDATED', topology_rev=?, validation_json=?, "
                                              "updated_utc=? WHERE block_id=? AND version=?",
                                              (topo["rev"], json.dumps(doc), _now(), block_id, version)), wait=True)
            self.h.event("BLOCK_VALIDATED", {"block": f"{block_id}@{version}", "topology_rev": topo["rev"]}, actor=actor)
            return self.get(block_id, version)

    def retire(self, actor: str, block_id: str, dependents: list[str]) -> dict:
        if dependents:
            raise BlockRefused(f"{block_id} is used by {', '.join(dependents)}: change or retire those programs first")
        with self._lock:
            last = self.latest().get(block_id)
            if last is None:
                raise BlockRefused(f"no block {block_id}")
            self.h.submit(lambda c: c.execute("UPDATE blocks SET status='RETIRED', updated_utc=? WHERE block_id=? AND "
                                              "version=?", (_now(), block_id, last["version"])), wait=True)
            self.h.event("BLOCK_RETIRED", {"block": f"{block_id}@{last['version']}"}, actor=actor)
            return self.get(block_id, last["version"])

    def stale(self, block: dict) -> bool:
        """A VALIDATED block whose graph has changed semantically since: shown, and revalidated before use."""
        v = block.get("validation") or {}
        return block["status"] == "VALIDATED" and v.get("topology_digest") != self.topology()["digest"]
