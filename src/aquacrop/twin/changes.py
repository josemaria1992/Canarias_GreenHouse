"""Equipment and program changes made in the digital twin, and their export to the real system (0.6.0).

The twin starts from the live snapshot. Changes made here are kept as a change set on top of it
(`changes.json` in the twin's state directory): equipment items created, changed or retired, and channel
assignments changed. Program versions made in the twin live in the twin's own program store (every version not
created by "live-snapshot"). The twin runs the snapshot with the change set applied, so what it simulates is
exactly what would be exported.

An export (`evidence/export-<digest12>.json`, written by the twin only) carries every change with what it
replaces on the real system (its base: the item version, the channel, the program's latest version as the twin
last imported them), and the proof: for every program version, the twin runs of exactly that body that ended
DONE in the current twin run. A program without such a run cannot be exported. Equipment not used by any
program that ran is marked "not exercised in the twin" for the approver.

Nothing here touches the real system: the live service reads the export, shows its differences, and applies it
only when an administrator or an operator with commissioning approves it (`aquacrop.api.changes`).
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from ..equipment.definitions import ICONS, ItemBody, TypeBody, channel_issues, item_issues
from ..programs.definitions import digest as program_digest, parse as program_parse

EXPORT_PREFIX = "export-"
KIND = "AQUACROP_TWIN_EXPORT"


class ChangeRefused(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def package_digest(doc: dict) -> str:
    body = {k: v for k, v in doc.items() if k != "digest"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def program_items(body: dict) -> set[str]:
    """Every equipment item a program body names (trigger and action)."""
    out = set()

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k in ("item", "meter", "sensor") and isinstance(v, str):
                    out.add(v)
                else:
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(body)
    return out


class TwinChanges:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.doc = {"items": {}, "retire": {}, "bindings": {}}
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            self.doc = {"items": d.get("items", {}), "retire": d.get("retire", {}), "bindings": d.get("bindings", {})}
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.doc, indent=1, sort_keys=True), encoding="utf-8")
        for attempt in range(5):                      # Windows may hold the old file for a moment
            try:
                tmp.replace(self.path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05)

    def empty(self) -> bool:
        return not any(self.doc.values())

    # ------------------------------------------------------------------ the snapshot with the changes applied
    @staticmethod
    def _live_items(snap: dict) -> dict[str, dict]:
        return {i["item_id"]: i for i in snap["equipment"]["items"]}

    @staticmethod
    def _types(snap: dict) -> dict[tuple[str, int], dict]:
        return {(t["type_id"], t["version"]): t for t in snap["equipment"]["types"]}

    def overlay(self, snap: dict) -> dict:
        s = copy.deepcopy(snap)
        items = self._live_items(s)
        for iid, ch in self.doc["items"].items():
            base = items.get(iid)
            items[iid] = {"item_id": iid, "version": (base["version"] if base else 0) + 1, "status": "ACTIVE",
                          "name": ch["name"], "type_id": ch["type_id"], "type_version": ch["type_version"],
                          "icon": ch["icon"], "body": ch["body"]}
        for iid in self.doc["retire"]:
            if iid in items:
                items[iid] = dict(items[iid], status="RETIRED")
        s["equipment"]["items"] = list(items.values())
        binds = {(b["item_id"], b["role"]): b for b in s.get("bindings", [])}
        for key, ch in self.doc["bindings"].items():
            iid, role = key.split(".", 1)
            if ch["point"] is None:
                binds.pop((iid, role), None)
            else:
                binds[(iid, role)] = {"item_id": iid, "role": role, "point": ch["point"], "status": "PROPOSED"}
        s["bindings"] = [b for (iid, _), b in binds.items() if iid not in self.doc["retire"]]
        return s

    # ------------------------------------------------------------------ edits
    def save_item(self, snap: dict, cfg, item_id: str, name: str, type_id: str, type_version: int, icon: str,
                  body: dict) -> dict:
        import re
        if not re.fullmatch(r"[a-z0-9][a-z0-9_\-]{0,47}", item_id or ""):
            raise ChangeRefused("equipment id: 1-48 lower-case letters, digits, - or _")
        name = (name or "").strip()
        if not 1 <= len(name) <= 80:
            raise ChangeRefused("give the equipment a name of 1-80 characters")
        if icon not in ICONS:
            raise ChangeRefused(f"icon {icon!r} is not a built-in icon")
        t = self._types(snap).get((type_id, type_version))
        if t is None or t["status"] not in ("ACTIVE", "SUPERSEDED"):
            raise ChangeRefused(f"type {type_id} v{type_version} is not active on the real system")
        try:
            ib = ItemBody.model_validate(body or {})
        except Exception as e:
            raise ChangeRefused(f"equipment details: {e}")
        issues = item_issues(TypeBody.model_validate(t["body"]), ib, icon)
        if issues:
            raise ChangeRefused("; ".join(issues))
        cur = self.overlay(snap)
        for other in cur["equipment"]["items"]:
            if other["item_id"] != item_id and other["status"] != "RETIRED" and other["name"].casefold() == name.casefold():
                raise ChangeRefused(f"another equipment item is already called {name}")
        live = self._live_items(snap).get(item_id)
        if live is not None and live["status"] == "RETIRED":
            raise ChangeRefused(f"{item_id} is retired on the real system: use another id")
        new = {"name": name, "type_id": type_id, "type_version": type_version, "icon": icon,
               "body": ib.model_dump(mode="json"), "base_version": live["version"] if live else None}
        if live is not None and all(live[k] == new[k] for k in ("name", "type_id", "type_version", "icon", "body")):
            self.doc["items"].pop(item_id, None)           # back to what the real system has
        else:
            self.doc["items"][item_id] = new
        self.doc["retire"].pop(item_id, None)
        self.save()
        return new

    def retire_item(self, snap: dict, item_id: str, users: list[str]) -> None:
        if users:
            raise ChangeRefused(f"{item_id} is used by {', '.join(users)}: change those programs first")
        live = self._live_items(snap).get(item_id)
        self.doc["items"].pop(item_id, None)
        for key in [k for k in self.doc["bindings"] if k.startswith(item_id + ".")]:
            self.doc["bindings"].pop(key)
        if live is not None and live["status"] != "RETIRED":
            self.doc["retire"][item_id] = {"base_version": live["version"]}
        self.save()

    def bind(self, snap: dict, cfg, item_id: str, role: str, point: str | None) -> None:
        cur = self.overlay(snap)
        it = next((i for i in cur["equipment"]["items"] if i["item_id"] == item_id and i["status"] != "RETIRED"), None)
        if it is None:
            raise ChangeRefused(f"equipment {item_id} does not exist in the twin")
        tb = TypeBody.model_validate(self._types(snap)[(it["type_id"], it["type_version"])]["body"])
        r = next((x for x in tb.roles if x.role == role), None)
        if r is None:
            raise ChangeRefused(f"{it['name']} has no role {role}")
        if point:
            issues = channel_issues(cfg, r, point, ItemBody.model_validate(it["body"]).scaling.get(role))
            if issues:
                raise ChangeRefused("; ".join(issues))
            other = next((b for b in cur["bindings"] if b["point"] == point and (b["item_id"], b["role"]) != (item_id, role)), None)
            if other:
                raise ChangeRefused(f"{point} already carries {other['item_id']}.{other['role']}")
        live = next((b for b in snap.get("bindings", []) if b["item_id"] == item_id and b["role"] == role), None)
        base = live["point"] if live else None
        key = f"{item_id}.{role}"
        if (point or None) == base:
            self.doc["bindings"].pop(key, None)
        else:
            self.doc["bindings"][key] = {"point": point or None, "base_point": base}
        self.save()

    def revert(self, item_id: str) -> None:
        self.doc["items"].pop(item_id, None)
        self.doc["retire"].pop(item_id, None)
        for key in [k for k in self.doc["bindings"] if k.startswith(item_id + ".")]:
            self.doc["bindings"].pop(key)
        self.save()

    def reset(self) -> None:
        self.doc = {"items": {}, "retire": {}, "bindings": {}}
        self.save()


# ---------------------------------------------------------------------- the export package
def twin_programs(snap: dict, versions: list[dict]) -> list[dict]:
    """Program versions made in the twin, the latest per program, with the real system's latest version (base)."""
    live_latest: dict[str, dict] = {}
    for p in snap.get("programs", []):
        if p["program_id"] not in live_latest or live_latest[p["program_id"]]["version"] < p["version"]:
            live_latest[p["program_id"]] = p
    live_digests = {(p["program_id"], p["digest"]) for p in snap.get("programs", [])}
    mine: dict[str, dict] = {}
    for v in versions:
        if v["created_by"] == "live-snapshot" or v["status"] == "RETIRED"                 or (v["program_id"], v["digest"]) in live_digests:
            continue
        if v["program_id"] not in mine or mine[v["program_id"]]["version"] < v["version"]:
            mine[v["program_id"]] = v
    out = []
    for pid, v in sorted(mine.items()):
        base = live_latest.get(pid)
        out.append({"program_id": pid, "name": v["name"], "body": v["body"], "digest": v["digest"],
                    "twin_version": v["version"], "status": v["status"],
                    "base_version": base["version"] if base else None, "base_body": base["body"] if base else None,
                    "base_name": base["name"] if base else None})
    return out


def preview(snap: dict, ch: TwinChanges, versions: list[dict], results: list[dict]) -> dict:
    """What an export would carry now, with its proof and what is missing."""
    live_items = TwinChanges._live_items(snap)
    progs = twin_programs(snap, versions)
    for p in progs:
        runs = [r for r in results if r.get("program_id") == p["program_id"] and r.get("version") == p["twin_version"]]
        p["runs"] = runs[-20:]
        p["passed"] = any(r.get("result") == "DONE" for r in runs)
        p["failed"] = sum(1 for r in runs if r.get("result") != "DONE")
    exercised: dict[str, list[str]] = {}
    for v in versions:
        if any(r.get("program_id") == v["program_id"] and r.get("version") == v["version"] and r.get("result") == "DONE"
               for r in results):
            for iid in program_items(v["body"]):
                exercised.setdefault(iid, []).append(f"{v['program_id']} v{v['version']}")
    items = []
    for iid, c in sorted(ch.doc["items"].items()):
        live = live_items.get(iid)
        items.append({"item_id": iid, "op": "update" if live else "create", **{k: c[k] for k in ("name", "type_id", "type_version", "icon", "body", "base_version")},
                      "before": {k: live[k] for k in ("name", "type_id", "type_version", "icon", "body")} if live else None,
                      "exercised_by": exercised.get(iid, [])})
    for iid, c in sorted(ch.doc["retire"].items()):
        live = live_items[iid]
        items.append({"item_id": iid, "op": "retire", "name": live["name"], "base_version": c["base_version"],
                      "before": {k: live[k] for k in ("name", "type_id", "type_version", "icon", "body")}, "exercised_by": []})
    binds = [{"item_id": k.split(".", 1)[0], "role": k.split(".", 1)[1], "point": c["point"], "base_point": c["base_point"]}
             for k, c in sorted(ch.doc["bindings"].items())]
    missing = [f"{p['name']} (v{p['twin_version']} in the twin) has no run that ended DONE in this twin run" for p in progs if not p["passed"]]
    missing += [f"{p['name']} (v{p['twin_version']}) is not validated in the twin" for p in progs if p["status"] != "VALIDATED"]
    return {"items": items, "bindings": binds, "programs": progs, "missing": missing,
            "empty": not (items or binds or progs)}


def write_export(feed_dir: Path, pv: dict, *, by: str, note: str, twin_run: str, snapshot_utc: str | None) -> dict:
    if pv["empty"]:
        raise ChangeRefused("nothing to export: the twin has no changes against the real system")
    if pv["missing"]:
        raise ChangeRefused("the twin has no proof yet: " + "; ".join(pv["missing"]))
    for p in pv["programs"]:
        if p["digest"] != program_digest(program_parse(p["body"]).model_dump(mode="json")):
            raise ChangeRefused(f"{p['program_id']}: its body does not match its digest")
    doc = {"kind": KIND, "by": by, "note": note, "written_utc": _now(), "twin_run": twin_run,
           "base_snapshot_utc": snapshot_utc, "items": pv["items"], "bindings": pv["bindings"],
           "programs": [{k: v for k, v in p.items() if k != "status"} for p in pv["programs"]],
           "meaning": "logic test on virtual modules with DEMO dynamics; not physical verification"}
    doc["digest"] = package_digest(doc)
    path = Path(feed_dir) / "evidence" / f"{EXPORT_PREFIX}{doc['digest'][:12]}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True, default=str), encoding="utf-8")
    os.chmod(tmp, 0o640)
    os.replace(tmp, path)
    return {"digest": doc["digest"], "written_utc": doc["written_utc"], "items": len(doc["items"]),
            "bindings": len(doc["bindings"]), "programs": len(doc["programs"])}


def read_exports(feed_dir: Path) -> list[dict]:
    out = []
    d = Path(feed_dir) / "evidence"
    if d.is_dir():
        for f in d.glob(f"{EXPORT_PREFIX}*.json"):
            try:
                doc = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if doc.get("kind") == KIND and doc.get("digest") == package_digest(doc):
                out.append(doc)
    return sorted(out, key=lambda r: r.get("written_utc", ""), reverse=True)
