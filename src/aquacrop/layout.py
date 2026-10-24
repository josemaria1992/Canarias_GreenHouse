"""The installation diagram as data (0.5.6, owner request).

A layout is a list of symbols (tanks, pumps, valves, sensors, sectors, boxes and text) with their positions, and
of pipes drawn as polylines. A symbol may be linked to an equipment item (and, for a sensor or tank, to one of
the item's input roles); an unlinked symbol is drawn as "not linked" and shows no state. Nothing in a layout
decides what a channel does: it is a picture of the equipment, and the equipment pages stay the only place
where items and channels are defined.

The layout is edited in the digital twin only (owner decision 2026-10-24). The twin sends a proposal through
the feed directory (`evidence/layout-<digest12>.json`); on the real system an administrator, or an operator with
commissioning, accepts it, and the Overview then draws it read-only. Every accepted layout is kept, so the
previous one can be restored.

The default layout is the 0.5.5 Overview drawing, so nothing looks different until someone changes it.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

KIND = "AQUACROP_LAYOUT"
PROPOSAL_PREFIX = "layout-"
NodeKind = Literal["tank", "pump", "valve", "sensor", "sector", "box", "label"]


class _B(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Node(_B):
    id: str = Field(pattern=r"^[A-Za-z0-9_.\-]{1,48}$")
    kind: NodeKind
    x: float = Field(ge=-50, le=5000)
    y: float = Field(ge=-50, le=5000)
    w: float | None = Field(default=None, gt=0, le=2000)
    h: float | None = Field(default=None, gt=0, le=2000)
    label: str = Field(default="", max_length=80)
    sub: str = Field(default="", max_length=120)         # a second line (sector channels, a note)
    item: str | None = Field(default=None, max_length=48)  # linked equipment item
    role: str | None = Field(default=None, max_length=48)  # the item's input role a sensor or tank shows
    tag: str = Field(default="", max_length=4)             # a sensor's letter: L, F, P, pH, EC, T
    dir: Literal["right", "left", "down", "up"] = "right"
    vertical: bool = False
    capacity_L: float | None = Field(default=None, gt=0, le=1e7)
    full_at: float | None = Field(default=None, gt=0, le=1e6)  # tank: the sensor reading that means full (0.6.2)
    planned: bool = False                                   # drawn, but the equipment does not exist yet


class Pipe(_B):
    id: str = Field(pattern=r"^[A-Za-z0-9_.\-]{1,48}$")
    points: list[tuple[float, float]] = Field(min_length=2, max_length=40)
    item: str | None = Field(default=None, max_length=48)        # water moves while this item runs or is open
    flow_item: str | None = Field(default=None, max_length=48)   # a flow meter on this route
    flow_role: str | None = Field(default=None, max_length=48)
    from_node: str | None = Field(default=None, max_length=48)   # the symbol its first end is attached to (0.6.2)
    to_node: str | None = Field(default=None, max_length=48)     # the symbol its last end is attached to


class Layout(_B):
    kind: Literal["AQUACROP_LAYOUT"] = KIND
    title: str = Field(default="Installation", max_length=80)
    width: float = Field(default=1100, ge=200, le=5000)
    height: float = Field(default=700, ge=200, le=5000)
    nodes: list[Node] = Field(default_factory=list, max_length=400)
    pipes: list[Pipe] = Field(default_factory=list, max_length=400)

    @field_validator("nodes")
    @classmethod
    def _unique_nodes(cls, v):
        ids = [n.id for n in v]
        if len(ids) != len(set(ids)):
            raise ValueError("two symbols have the same id")
        return v

    @field_validator("pipes")
    @classmethod
    def _unique_pipes(cls, v):
        ids = [p.id for p in v]
        if len(ids) != len(set(ids)):
            raise ValueError("two pipes have the same id")
        return v


def parse(doc: dict) -> Layout:
    return Layout.model_validate(doc)


def canonical(lay: Layout) -> dict:
    return lay.model_dump(mode="json", exclude_defaults=False)


def digest(lay: Layout) -> str:
    return hashlib.sha256(json.dumps(canonical(lay), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def issues(lay: Layout, items: dict[str, dict]) -> list[str]:
    """What does not match the equipment: shown beside the layout, never a reason to refuse it (a symbol may
    be drawn before its item exists; it is then drawn as planned or not linked)."""
    out = []
    for n in lay.nodes:
        if n.item and n.item not in items:
            out.append(f"{n.label or n.id}: linked to {n.item}, which is not in the equipment list")
    for p in lay.pipes:
        for it in (p.item, p.flow_item):
            if it and it not in items:
                out.append(f"pipe {p.id}: linked to {it}, which is not in the equipment list")
    return out


def summary(old: Layout | None, new: Layout) -> dict:
    """Added, removed, moved and changed symbols and pipes: what an approver reads before accepting."""
    o_n = {n.id: n for n in (old.nodes if old else [])}
    n_n = {n.id: n for n in new.nodes}
    o_p = {p.id: p for p in (old.pipes if old else [])}
    n_p = {p.id: p for p in new.pipes}
    moved = [i for i in n_n if i in o_n and (n_n[i].x, n_n[i].y, n_n[i].w, n_n[i].h) != (o_n[i].x, o_n[i].y, o_n[i].w, o_n[i].h)]
    changed = [i for i in n_n if i in o_n and i not in moved and n_n[i] != o_n[i]]
    name = lambda d, i: d[i].label or i                                          # noqa: E731
    return {"added": [name(n_n, i) for i in n_n if i not in o_n], "removed": [name(o_n, i) for i in o_n if i not in n_n],
            "moved": [name(n_n, i) for i in moved], "changed": [name(n_n, i) for i in changed],
            "pipes_added": [i for i in n_p if i not in o_p], "pipes_removed": [i for i in o_p if i not in n_p],
            "pipes_changed": [i for i in n_p if i in o_p and n_p[i] != o_p[i]]}


# ---------------------------------------------------------------------- readings for the drawing
def item_readings(views: dict, samples: dict, *, commanded: dict[str, bool] | None = None,
                  faults: dict[str, str] | None = None) -> dict[str, dict]:
    """Per item: output readback, feedback and every input role's value, from the acquired samples.
    UNKNOWN stays UNKNOWN (never OFF); a role without a channel is "no channel"."""
    out = {}
    for iid, it in views.items():
        cp = it.command_point
        s = samples.get(cp) if cp else None
        coil = "NONE" if cp is None else ("UNKNOWN" if s is None or s.value is None else ("ON" if s.value else "OFF"))
        fb = it.feedback_role
        feedback = "NONE"
        values = {}
        for r in it.type.roles:
            if r.direction.value != "INPUT":
                continue
            p = it.points.get(r.role)
            s = samples.get(p) if p else None
            q = s.quality.value if s is not None else "NO CHANNEL"
            if s is None:
                val = None
            elif s.value is not None:
                val = (not bool(s.value)) if r.inverted else bool(s.value)
            elif s.raw_value is not None:
                sc = it.body.scaling.get(r.role)
                val = round(sc.apply(float(s.raw_value) / 1000.0), 3) if sc else None
            else:
                val = None
            values[r.role] = {"value": val, "unit": r.unit, "quality": q, "label": r.label}
            if fb is not None and r.role == fb.role:
                feedback = "UNKNOWN" if val is None or q not in ("GOOD", "OVERRIDDEN") else ("ON" if val else "OFF")
        out[iid] = {"name": it.name, "coil": coil, "feedback": feedback, "values": values,
                    "commanded": bool((commanded or {}).get(iid)), "fault": (faults or {}).get(iid)}
    return out


# ---------------------------------------------------------------------- storage
def _atomic(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True), encoding="utf-8")
    os.chmod(tmp, 0o640)
    os.replace(tmp, path)


class LiveLayouts:
    """The real system's accepted layouts: `current.json` and every accepted one under `accepted/`."""

    def __init__(self, directory: Path):
        self.dir = Path(directory)

    def current(self) -> tuple[Layout, dict]:
        p = self.dir / "current.json"
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            return parse(doc["layout"]), {k: v for k, v in doc.items() if k != "layout"}
        except (OSError, ValueError, KeyError):
            return default_layout(), {"source": "default", "digest": digest(default_layout())}

    def accept(self, lay: Layout, actor: str, *, source: str, note: str = "") -> dict:
        prev, meta = self.current()
        d = digest(lay)
        rec = {"layout": canonical(lay), "digest": d, "accepted_by": actor, "source": source, "note": note,
               "accepted_utc": datetime.now(timezone.utc).isoformat(), "previous": meta.get("digest")}
        _atomic(self.dir / "accepted" / f"{d[:16]}.json", rec)
        _atomic(self.dir / "current.json", rec)
        return {k: v for k, v in rec.items() if k != "layout"}

    def get(self, d: str) -> Layout | None:
        if d == digest(default_layout()):
            return default_layout()
        p = self.dir / "accepted" / f"{d[:16]}.json"
        try:
            return parse(json.loads(p.read_text(encoding="utf-8"))["layout"])
        except (OSError, ValueError, KeyError):
            return None


def write_proposal(feed_dir: Path, lay: Layout, *, by: str, note: str, twin_run: str) -> dict:
    d = digest(lay)
    rec = {"kind": "AQUACROP_LAYOUT_PROPOSAL", "layout": canonical(lay), "digest": d, "by": by, "note": note,
           "twin_run": twin_run, "written_utc": datetime.now(timezone.utc).isoformat()}
    _atomic(Path(feed_dir) / "evidence" / f"{PROPOSAL_PREFIX}{d[:12]}.json", rec)
    return {k: v for k, v in rec.items() if k != "layout"}


def read_proposals(feed_dir: Path) -> list[dict]:
    """Proposals the twin sent, newest first; one whose layout no longer matches its digest is left out."""
    out = []
    d = Path(feed_dir) / "evidence"
    if d.is_dir():
        for f in d.glob(f"{PROPOSAL_PREFIX}*.json"):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
                lay = parse(rec["layout"])
            except (OSError, ValueError, KeyError):
                continue
            if rec.get("kind") == "AQUACROP_LAYOUT_PROPOSAL" and digest(lay) == rec.get("digest"):
                out.append(rec)
    return sorted(out, key=lambda r: r.get("written_utc", ""), reverse=True)


# ---------------------------------------------------------------------- the 0.5.5 drawing as data
def default_layout() -> Layout:
    n: list[dict] = []
    p: list[dict] = []
    n.append({"id": "t-mains", "kind": "label", "x": 20, "y": 26, "label": "Mains water"})
    p.append({"id": "mains", "points": [(20, 40), (620, 40)]})
    p.append({"id": "fill-main", "points": [(620, 40), (620, 210)]})
    for s, name, x in (("a", "Fertiliser A", 60), ("b", "Fertiliser B", 200), ("acid", "Acid", 340)):
        c = x + 45
        tp = "TP acid" if s == "acid" else f"TP {s.upper()}"
        n += [{"id": f"v-fill-{s}", "kind": "valve", "x": c, "y": 80, "label": "water in", "vertical": True},
              {"id": f"tank-{s}", "kind": "tank", "x": x, "y": 125, "w": 90, "h": 100, "label": name, "capacity_L": 100},
              {"id": f"lvl-{s}", "kind": "sensor", "x": x + 78, "y": 137, "tag": "L", "label": f"{name} level"},
              {"id": f"v-out-{s}", "kind": "valve", "x": c, "y": 290, "label": "outlet", "vertical": True},
              {"id": f"tp-{s}", "kind": "pump", "x": c, "y": 352, "label": tp, "dir": "down"},
              {"id": f"flow-tp-{s}", "kind": "sensor", "x": c + 22, "y": 416, "tag": "F", "label": f"{tp} flow"}]
        p += [{"id": f"fill-{s}", "points": [(c, 40), (c, 125)]},
              {"id": f"dose-{s}", "points": [(c, 262), (c, 430), (500, 430)]}]
    n.append({"id": "t-mixing", "kind": "label", "x": 20, "y": 470, "label": "No stirrer or air pump: mixing is not checked."})
    n += [{"id": "v-fill-main", "kind": "valve", "x": 620, "y": 128, "label": "water in", "vertical": True},
          {"id": "flow-fill-main", "kind": "sensor", "x": 642, "y": 180, "tag": "F", "label": "Main tank fill flow"},
          {"id": "tank-main", "kind": "tank", "x": 500, "y": 210, "w": 160, "h": 240, "label": "Main tank", "capacity_L": 1000},
          {"id": "lvl-main", "kind": "sensor", "x": 508, "y": 232, "tag": "L", "label": "Main tank level"},
          {"id": "ph", "kind": "sensor", "x": 530, "y": 515, "tag": "pH", "label": "pH"},
          {"id": "ec", "kind": "sensor", "x": 580, "y": 515, "tag": "EC", "label": "EC"},
          {"id": "temp", "kind": "sensor", "x": 630, "y": 515, "tag": "T", "label": "Temperature"},
          {"id": "t-probe", "kind": "label", "x": 505, "y": 560, "label": "probe position proposed, not installed"},
          {"id": "mp1", "kind": "pump", "x": 760, "y": 290, "label": "Main pump 1"},
          {"id": "mp2", "kind": "pump", "x": 760, "y": 410, "label": "Main pump 2"},
          {"id": "drive", "kind": "box", "x": 690, "y": 478, "w": 140, "h": 50, "label": "Pump drive (shared)"}]
    for i, (yp) in enumerate((290, 410), start=1):
        p.append({"id": f"to-mp{i}", "points": [(660, 410), (700, 410), (700, yp), (744, yp)]})
        for j, ys in enumerate((200, 440), start=1):
            p.append({"id": f"mp{i}-s{j}", "points": [(776, yp), (830, yp), (830, ys), (867, ys)]})
    for j, (ys, ch, outlets) in enumerate(((200, "1–6", 24), (440, "7–13", 28)), start=1):
        n += [{"id": f"v-s{j}", "kind": "valve", "x": 880, "y": ys, "label": f"valve S{j}"},
              {"id": f"flow-s{j}", "kind": "sensor", "x": 912, "y": ys - 22, "tag": "F", "label": f"Sector {j} flow"},
              {"id": f"pres-s{j}", "kind": "sensor", "x": 912, "y": ys + 24, "tag": "P", "label": f"Sector {j} pressure"},
              {"id": f"sector-{j}", "kind": "sector", "x": 930, "y": ys - 52, "w": 160, "h": 104, "label": f"Sector {j}",
               "sub": f"channels {ch} · {outlets} outlets"}]
        p += [{"id": f"s{j}", "points": [(893, ys), (930, ys)]},
              {"id": f"drain-s{j}", "points": [(1090, ys + 30), (1097, ys + 30), (1097, 600), (960, 600)]}]
    n += [{"id": "t-gravity", "kind": "label", "x": 975, "y": 592, "label": "by gravity, not measured"},
          {"id": "tank-sump", "kind": "tank", "x": 860, "y": 560, "w": 100, "h": 80, "label": "Drainage tank", "capacity_L": 200},
          {"id": "lvl-sump", "kind": "sensor", "x": 946, "y": 574, "tag": "L", "label": "Drainage tank level"},
          {"id": "rp1", "kind": "pump", "x": 770, "y": 620, "label": "Return pump 1", "dir": "left"},
          {"id": "rp2", "kind": "pump", "x": 670, "y": 620, "label": "Return pump 2", "dir": "left"},
          {"id": "flow-return", "kind": "sensor", "x": 520, "y": 640, "tag": "F", "label": "Return flow"},
          {"id": "t-safety", "kind": "label", "x": 20, "y": 690, "label": "Safety circuit: not measured"}]
    p.append({"id": "return", "points": [(860, 620), (478, 620), (478, 440), (500, 440)]})
    return Layout(title="Installation", nodes=n, pipes=p)
