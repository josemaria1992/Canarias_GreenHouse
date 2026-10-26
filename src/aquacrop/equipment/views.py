"""Equipment as the program compiler, the block checks and the graph editor see it (0.7.0).

* `design_views`: one ItemView per active item with every channel assigned to it, proposed or verified. It is for
  drafting and checking structure only; the live engine resolves VERIFIED channels alone (`programs/live.py`).
* `hydraulic_items`: item id -> {"kind": the type's water kind or None, "name": ...}, for the graph.
"""
from __future__ import annotations

from ..programs.engine import ItemView
from .definitions import ItemBody, TypeBody


def design_views(equipment, *, verified_only: bool = False) -> dict[str, ItemView]:
    types = {(t["type_id"], t["version"]): TypeBody.model_validate(t["body"]) for t in equipment.types()}
    binds = equipment.bindings()
    out = {}
    for it in equipment.items(include_retired=False):
        tb = types.get((it["type_id"], it["type_version"]))
        if tb is None:
            continue
        mine = {r: b for (iid, r), b in binds.items() if iid == it["item_id"]
                and (not verified_only or b["status"] == "VERIFIED")}
        out[it["item_id"]] = ItemView(it["item_id"], it["name"], tb, ItemBody.model_validate(it["body"]),
                                      {r: b["point"] for r, b in mine.items()},
                                      {r: b["status"] == "VERIFIED" for r, b in mine.items()})
    return out


def hydraulic_items(equipment) -> dict[str, dict]:
    types = {(t["type_id"], t["version"]): TypeBody.model_validate(t["body"]) for t in equipment.types()}
    out = {}
    for it in equipment.items(include_retired=False):
        tb = types.get((it["type_id"], it["type_version"]))
        out[it["item_id"]] = {"kind": tb.hydraulic if tb else None, "name": it["name"],
                              "public_id": it.get("public_id", it["item_id"])}
    return out


def limit_inputs(views: dict[str, ItemView], samples: dict, now_utc) -> list[dict]:
    """The items' configured alarm limits with their current engineering value (0.7.0, ALM-01). A value that is
    missing, stale, bad or unscaled is None (UNKNOWN); a limit on a role with no channel is skipped."""
    from ..domain.model import Quality
    usable = {Quality.GOOD, Quality.UNSCALED, Quality.OVERRIDDEN}
    out = []
    for iid, v in views.items():
        for a in v.body.alarms:
            p = v.points.get(a.role)
            sc = v.body.scaling.get(a.role)
            role = next((r for r in v.type.roles if r.role == a.role), None)
            if p is None or role is None:
                continue
            s = samples.get(p)
            val = None
            if (s is not None and sc is not None and s.quality in usable and s.raw_value is not None
                    and s.raw_unit in ("uA", "mV") and (now_utc - s.acquired_utc).total_seconds() <= v.body.stale_after_s
                    and (s.raw_unit == "uA") == (sc.raw_unit == "mA")):
                val = sc.apply(float(s.raw_value) / 1000.0)
            out.append({"item": iid, "name": v.name, "role": a.role, "label": role.label, "unit": sc.unit if sc else role.unit,
                        "value": val, "low": a.low, "high": a.high, "deadband": a.deadband,
                        "delay_s": getattr(a, "delay_s", 0.0), "quality": s.quality.value if s else "NO SAMPLE",
                        "origin": getattr(getattr(s, "origin", None), "value", None)})
    return out


def views_hydraulic(views: dict[str, ItemView]) -> dict[str, dict]:
    """The same list from item views (the twin's items)."""
    return {iid: {"kind": v.type.hydraulic, "name": v.name} for iid, v in views.items()}
