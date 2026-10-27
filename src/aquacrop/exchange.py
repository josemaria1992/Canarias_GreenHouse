"""Project exchange (0.7.3, IMPORT-01): the installation's design as one JSON or YAML document, and its import with a
preview of every conflict before anything is written.

Export: equipment types and items, blocks, programs (the latest version of each) and the installation drawing.
Never exported: users, passwords, sessions, bench leases, secrets and keys, channel bindings (they belong to one
installation's wiring and are verified there) and history.

Import: the document is read with a size and depth limit, every record is checked by the same parsers as the pages
use, and the preview names each record NEW, SAME, NEW VERSION (becomes a DRAFT) or CONFLICT (never written). Applying
writes DRAFT types, blocks and programs only; items are listed as proposals to create on the Equipment page (so their
channels are chosen and verified here); the drawing is kept as a proposal file (not applied) and never changes the
Overview. Secrets, leases and sessions in a document refuse it; so do file references that leave their folder. A
background image is a reference only: it never creates a connection.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

import yaml

KIND = "AQUACROP_PROJECT"
SCHEMA = 1
MAX_BYTES = 2 * 1024 * 1024
MAX_DEPTH = 40
TOP = {"kind", "schema", "exported_utc", "software", "config_id", "equipment", "blocks", "programs", "drawing",
       "excluded", "note"}
FORBIDDEN = re.compile(r"pass(word)?|secret|token|api[_-]?key|private[_-]?key|lease|session|handoff|cookie|credential",
                       re.I)
FILE_KEYS = {"file", "image", "href", "background_file"}        # never "src": an edge's source port
SAFE_FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.\-]{0,119}")
EXCLUDED = ["users and passwords", "sessions", "bench leases", "secrets and keys", "channel bindings (verified on each "
            "installation)", "history"]


class ExchangeRefused(ValueError):
    pass


def export(equipment, blocks, programs, software: str, drawing: dict | None = None) -> dict:
    """`drawing`: the installation as the Overview draws it (C075-03): the explicit graph and its diagram, or, while
    the Overview still uses the earlier drawing, its faithful conversion together with that earlier layout and its
    digest, so nothing visible is lost in the exchange."""
    eq = equipment.export()
    latest_p: dict[str, dict] = {}
    for v in programs.versions():
        if v["status"] != "RETIRED":
            latest_p[v["program_id"]] = v
    if drawing is None:
        drawing = {"graph": blocks.topology()["graph"], "diagram": blocks.diagram("installation").get("diagram") or {}}
    return {"kind": KIND, "schema": SCHEMA, "exported_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "software": software, "config_id": eq.get("config_id"),
            "equipment": {"types": eq["types"], "items": eq["items"]},
            "blocks": [{"block_id": b["block_id"], "name": b["name"], "version": b["version"], "status": b["status"],
                        "body": b["body"]} for b in blocks.latest().values() if b["status"] != "RETIRED"],
            "programs": [{"program_id": v["program_id"], "name": v["name"], "version": v["version"], "status": v["status"],
                          "body": v["body"]} for v in latest_p.values()],
            "drawing": drawing,
            "excluded": EXCLUDED,
            "note": "Import makes DRAFT types, blocks and programs; items and the drawing arrive as proposals."}


def dumps(doc: dict, fmt: str) -> str:
    if fmt == "yaml":
        return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
    return json.dumps(doc, indent=1, ensure_ascii=False)


def loads(text: str) -> dict:
    """JSON or YAML (safe loader only), refused when too large, too deep, or not one mapping."""
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ExchangeRefused(f"the document is larger than {MAX_BYTES // 1024} KiB")
    try:
        doc = json.loads(text) if text.lstrip()[:1] in "{[" else yaml.safe_load(text)
    except (ValueError, yaml.YAMLError) as e:
        raise ExchangeRefused(f"not readable JSON or YAML: {str(e).splitlines()[0][:200]}")
    if not isinstance(doc, dict):
        raise ExchangeRefused("the document is not one AquaCrop project")
    _walk(doc, 0, "")
    if doc.get("kind") != KIND or doc.get("schema") != SCHEMA:
        raise ExchangeRefused(f"not an AquaCrop project document (kind {KIND}, schema {SCHEMA})")
    extra = sorted(set(doc) - TOP)
    if extra:
        raise ExchangeRefused(f"unknown sections: {', '.join(extra)[:200]}")
    _shape(doc)
    return doc


MAX_RECORDS = 500
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-]{0,47}")


def _shape(doc: dict) -> None:
    """C075-07: the whole structure is checked before any record is read, so a malformed document is refused with
    the record it names and never half-written."""
    def records(where, x, key):
        if x is None:
            return
        if not isinstance(x, list):
            raise ExchangeRefused(f"{where}: a list of records is expected")
        if len(x) > MAX_RECORDS:
            raise ExchangeRefused(f"{where}: more than {MAX_RECORDS} records")
        seen = set()
        for i, r in enumerate(x):
            if not isinstance(r, dict):
                raise ExchangeRefused(f"{where}[{i}]: a record (a mapping with {key}) is expected")
            rid = r.get(key)
            if not isinstance(rid, str) or not ID_RE.fullmatch(rid):
                raise ExchangeRefused(f"{where}[{i}]: {key} must be 1-48 letters, digits, - or _")
            if rid in seen:
                raise ExchangeRefused(f"{where}: {rid} appears twice")
            seen.add(rid)
            if "body" in r and not isinstance(r["body"], dict):
                raise ExchangeRefused(f"{where}[{i}] ({rid}): body must be a mapping")
            if "name" in r and not isinstance(r["name"], str):
                raise ExchangeRefused(f"{where}[{i}] ({rid}): name must be text")
    eq = doc.get("equipment")
    if eq is not None and not isinstance(eq, dict):
        raise ExchangeRefused("equipment: a mapping with types and items is expected")
    records("equipment.types", (eq or {}).get("types"), "type_id")
    records("equipment.items", (eq or {}).get("items"), "item_id")
    records("blocks", doc.get("blocks"), "block_id")
    records("programs", doc.get("programs"), "program_id")
    dr = doc.get("drawing")
    if dr is not None:
        if not isinstance(dr, dict):
            raise ExchangeRefused("drawing: a mapping with graph and diagram is expected")
        for k in ("graph", "diagram", "legacy_layout", "source"):
            if dr.get(k) is not None and not isinstance(dr[k], dict):
                raise ExchangeRefused(f"drawing.{k}: a mapping is expected")


def _walk(x, depth: int, where: str) -> None:
    if depth > MAX_DEPTH:
        raise ExchangeRefused("the document is nested too deeply")
    if isinstance(x, dict):
        for k, v in x.items():
            if not isinstance(k, str):
                raise ExchangeRefused(f"{where or 'the document'}: a key is not text")
            if FORBIDDEN.search(k):
                raise ExchangeRefused(f"{where}.{k}: passwords, secrets, sessions and leases are never imported")
            if k in FILE_KEYS and v is not None and not (isinstance(v, str) and SAFE_FILE.fullmatch(v) and ".." not in v):
                raise ExchangeRefused(f"{where}.{k}: a file is named by a plain file name (no folders, no ..)")
            _walk(v, depth + 1, f"{where}.{k}" if where else k)
    elif isinstance(x, list):
        for i, v in enumerate(x):
            _walk(v, depth + 1, f"{where}[{i}]")
    elif isinstance(x, float) and x != x:
        raise ExchangeRefused(f"{where}: not a number")


def _d(x) -> str:
    return hashlib.sha256(json.dumps(x, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def preview(doc: dict, equipment, blocks, programs) -> dict:
    """Each record with what applying would do. Nothing is written."""
    from .blocks import definitions as bd
    from .equipment.definitions import ItemBody, TypeBody
    from .programs.definitions import parse as parse_program
    from . import diagram as dg
    from .topology.definitions import parse as parse_topology
    rows = []

    def row(kind, key, action, why="", target=None):
        rows.append({"kind": kind, "id": key, "action": action, "why": why, "target": target})

    eq = doc.get("equipment") or {}
    have_t: dict[str, list] = {}
    for t in equipment.types():
        have_t.setdefault(t["type_id"], []).append(t)
    for t in (eq.get("types") or []):
        tid = str(t.get("type_id"))
        try:
            body = TypeBody.model_validate(t.get("body")).model_dump(mode="json")
        except Exception as e:
            row("type", tid, "CONFLICT", f"not a valid type: {str(e)[:200]}")
            continue
        mine = have_t.get(tid, [])

        def norm(b):                                  # stored bodies from earlier versions, read as today's
            try:
                return _d(TypeBody.model_validate(b).model_dump(mode="json"))
            except Exception:
                return _d(b)
        if any(norm(m["body"]) == _d(body) for m in mine):
            row("type", tid, "SAME")
        elif any(m["status"] == "DRAFT" for m in mine):
            row("type", tid, "CONFLICT", "this installation has an unsaved draft of this type: it is never replaced")
        else:
            row("type", tid, "NEW VERSION" if mine else "NEW", "arrives as a DRAFT to review and activate")
    have_i = {i["item_id"]: i for i in equipment.items()}
    for i in (eq.get("items") or []):
        iid = str(i.get("item_id"))
        try:
            ItemBody.model_validate(i.get("body") or {})
        except Exception as e:
            row("item", iid, "CONFLICT", f"not a valid item: {str(e)[:200]}")
            continue
        cur = have_i.get(iid)
        if cur and cur["type_id"] != i.get("type_id"):
            row("item", iid, "CONFLICT", f"here {iid} is a {cur['type_id']}, there a {i.get('type_id')}")
        elif cur:
            row("item", iid, "SAME" if _d(cur["body"]) == _d(i.get("body") or {}) else "PROPOSAL",
                "" if _d(cur["body"]) == _d(i.get("body") or {}) else "differs: change it on the Equipment page if wanted")
        else:
            row("item", iid, "PROPOSAL", "create it on the Equipment page, then choose and verify its channels")
    known_items = set(have_i) | {str(i.get("item_id")) for i in (eq.get("items") or [])}
    lb = blocks.latest()
    for b in doc.get("blocks") or []:
        bid = str(b.get("block_id"))
        try:
            body = bd.parse(b.get("body") or {})
        except Exception as e:
            row("block", bid, "CONFLICT", f"not a valid block: {str(e)[:200]}")
            continue
        missing = sorted(set(body.members) - known_items)
        cur = lb.get(bid)
        if missing:
            row("block", bid, "CONFLICT", f"names equipment that does not exist: {', '.join(missing)[:200]}")
        elif cur and cur["digest"] == bd.digest(body):
            row("block", bid, "SAME")
        elif cur and cur["status"] == "DRAFT":
            row("block", bid, "CONFLICT", "this installation has a draft of this block: it is never replaced")
        else:
            row("block", bid, "NEW VERSION" if cur else "NEW", "arrives as a DRAFT to validate")
    have_p: dict[str, list] = {}
    for v in programs.versions():
        have_p.setdefault(v["program_id"], []).append(v)
    known_blocks = set(lb) | {str(b.get("block_id")) for b in doc.get("blocks") or []}
    for p in doc.get("programs") or []:
        pid = str(p.get("program_id"))
        try:
            body = parse_program(p.get("body") or {}).model_dump(mode="json")
        except Exception as e:
            row("program", pid, "CONFLICT", f"not a valid program: {str(e)[:200]}")
            continue
        blk = (body.get("authoring") or {}).get("block_id")
        mine = have_p.get(pid, [])
        if blk and blk not in known_blocks:
            row("program", pid, "CONFLICT", f"waters through block {blk}, which does not exist")
        elif any(m["digest"] == _d(body) for m in mine):
            row("program", pid, "SAME")
        elif any(m["status"] == "DRAFT" for m in mine):
            row("program", pid, "CONFLICT", "this installation has a draft of this program: it is never replaced")
        else:
            row("program", pid, "NEW VERSION" if mine else "NEW", "arrives as a DRAFT: validate and twin-test it")
    dr = doc.get("drawing")
    if dr:
        try:
            topo = parse_topology(dr.get("graph") or {"nodes": [], "edges": []})
            dia = dg.parse({"view": "installation", **{k: v for k, v in (dr.get("diagram") or {}).items()
                                                      if k not in ("background",)}})
            missing = sorted({n.item for n in topo.nodes if n.item} - known_items)
            row("drawing", "installation", "CONFLICT" if missing else "PROPOSAL",
                f"names equipment that does not exist: {', '.join(missing)[:200]}" if missing else
                f"{len(topo.nodes)} parts, {len(topo.edges)} explicit connections, {len(dia.symbols)} symbols: kept as "
                "a proposal file, not applied; the Overview is not changed"
                + ("; its background image is a reference only and creates no connection" if (dr.get("diagram") or {}).get("background") else ""))
        except Exception as e:
            row("drawing", "installation", "CONFLICT", f"not a valid drawing: {str(e)[:200]}")
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["action"]] = counts.get(r["action"], 0) + 1
    # C075-04: the approval covers this document, these actions AND the installation as it was previewed; any
    # change to a record the import could touch makes the preview stale
    state = {"types": sorted((t["type_id"], t["version"], t["status"], _d(t["body"])) for t in equipment.types()),
             "items": sorted((i["item_id"], i["version"], i["status"], _d(i["body"])) for i in equipment.items()),
             "blocks": sorted((b["block_id"], b["version"], b["status"], b["digest"]) for b in lb.values()),
             "programs": sorted((v["program_id"], v["version"], v["status"], v["digest"]) for v in programs.versions()),
             "drawing": [blocks.topology()["rev"], blocks.diagram("installation").get("rev")]}
    return {"digest": _d({"document": _d(doc), "rows": rows, "state": state}), "document": _d(doc), "rows": rows,
            "counts": counts, "excluded": EXCLUDED,
            "meaning": "Applying writes DRAFT types, blocks and programs only. Items and the drawing are proposals; "
                       "CONFLICT rows are never written. Nothing is activated, bound or run. If anything on this "
                       "installation changes before you apply, preview again."}


class StalePreview(ExchangeRefused):
    pass


def apply(doc: dict, actor: str, equipment, blocks, programs, proposals_dir, expected: str | None,
          drawing_dirs=()) -> dict:
    """Writes exactly what the preview `expected` showed; refuses when the installation changed since (the caller
    holds a lock around this call). A record that cannot be written stops the import and names what was written."""
    pv = preview(doc, equipment, blocks, programs)
    if expected != pv["digest"]:
        raise StalePreview("the installation or the document changed since this preview: preview it again and "
                           "check the rows before applying")
    want = {(r["kind"], r["id"]) for r in pv["rows"] if r["action"] in ("NEW", "NEW VERSION")}
    done = []
    eq = doc.get("equipment") or {}
    try:
        for t in eq.get("types") or []:
            if ("type", t["type_id"]) in want:
                equipment.save_type_draft(actor, t["type_id"], t.get("name") or t["type_id"], t["body"])
                done.append(("type", t["type_id"]))
        for b in doc.get("blocks") or []:
            if ("block", b["block_id"]) in want:
                cur = blocks.latest().get(b["block_id"])
                blocks.save_block(actor, b["block_id"], b.get("name") or b["block_id"], b["body"],
                                  expected_version=cur["version"] if cur else None)
                done.append(("block", b["block_id"]))
        for p in doc.get("programs") or []:
            if ("program", p["program_id"]) in want:
                programs.save_draft(actor, p["program_id"], p.get("name") or p["program_id"], p["body"])
                done.append(("program", p["program_id"]))
    except Exception as e:
        raise ExchangeRefused(f"stopped at a record that could not be written ({e}); already written as drafts: "
                              + (", ".join(f"{k} {i}" for k, i in done) or "nothing"))
    tag = pv["document"][:16]                                         # names of our own, never from the document
    kept = None
    items = [i for i in eq.get("items") or [] if any(r["kind"] == "item" and r["id"] == i["item_id"] and
                                                    r["action"] == "PROPOSAL" for r in pv["rows"])]
    drawing = any(r["kind"] == "drawing" and r["action"] == "PROPOSAL" for r in pv["rows"])
    if proposals_dir is not None and (items or drawing):
        proposals_dir.mkdir(parents=True, exist_ok=True)
        if items:
            _write(proposals_dir / f"items-{tag}.json", {"kind": "ITEM_PROPOSALS", "document": pv["document"],
                                                          "items": items, "types": eq.get("types") or []})
        if drawing:
            kept = f"drawing-{tag}.json"
            doc_d = {"kind": "DRAWING_PROPOSAL", "document": pv["document"], "drawing": doc["drawing"],
                     "conflicts": [r for r in pv["rows"] if r["action"] == "CONFLICT"]}
            for d in (proposals_dir, *drawing_dirs):
                d.mkdir(parents=True, exist_ok=True)
                _write(d / kept, doc_d)
    return {"digest": pv["digest"], "written": [{"kind": k, "id": i, "status": "DRAFT"} for k, i in done],
            "proposals": [r for r in pv["rows"] if r["action"] == "PROPOSAL"],
            "not_written": [r for r in pv["rows"] if r["action"] == "CONFLICT"], "drawing_proposal": kept}


PROPOSAL_NAME = re.compile(r"(items|drawing)-[0-9a-f]{16}\.json")


def _write(path, doc: dict) -> None:
    tmp = path.with_name(path.name + ".partial")
    tmp.write_text(json.dumps(doc, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
    try:
        path.chmod(0o640)                       # the twin service reads drawing proposals from the feed folder
    except OSError:
        pass


def read_proposal(folder, name: str) -> dict:
    if folder is None or not PROPOSAL_NAME.fullmatch(name or ""):
        raise ExchangeRefused("no such proposal")
    try:
        return json.loads((folder / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ExchangeRefused("no such proposal")


def item_proposals(folder, existing: set) -> list[dict]:
    """Imported items still to create on the Equipment page (C075-03): each record as it was imported, minus those
    that exist now."""
    out = []
    if folder is None or not folder.is_dir():
        return out
    for f in sorted(folder.glob("items-*.json")):
        if not PROPOSAL_NAME.fullmatch(f.name):
            continue
        d = read_proposal(folder, f.name)
        types = {t["type_id"]: t for t in d.get("types") or []}
        for i in d.get("items") or []:
            if i["item_id"] not in existing:
                out.append({"file": f.name, "item_id": i["item_id"], "name": i.get("name") or i["item_id"],
                            "type_id": i.get("type_id"), "icon": i.get("icon") or "", "body": i.get("body") or {},
                            "type_name": (types.get(i.get("type_id")) or {}).get("name")})
    return out


def drawing_proposals(folder) -> list[dict]:
    out = []
    if folder is None or not folder.is_dir():
        return out
    for f in sorted(folder.glob("drawing-*.json")):
        if PROPOSAL_NAME.fullmatch(f.name):
            d = read_proposal(folder, f.name)
            dia = (d.get("drawing") or {}).get("diagram") or {}
            out.append({"file": f.name, "document": d.get("document"), "conflicts": d.get("conflicts") or [],
                        "symbols": len(dia.get("symbols") or []), "placeholders": len(dia.get("placeholders") or []),
                        "connections": len(((d.get("drawing") or {}).get("graph") or {}).get("edges") or [])})
    return out


def dismiss(folder, name: str, item_id: str | None = None) -> None:
    d = read_proposal(folder, name)
    if item_id is None:
        (folder / name).unlink(missing_ok=True)
        return
    d["items"] = [i for i in d.get("items") or [] if i["item_id"] != item_id]
    if d["items"]:
        _write(folder / name, d)
    else:
        (folder / name).unlink(missing_ok=True)
