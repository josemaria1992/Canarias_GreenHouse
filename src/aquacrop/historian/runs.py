"""Program runs from the recorded history (0.7.0, Operation History): one row per operation the engine started, with
its outcome, measured and uncertain volume, and a timeline of what happened during it.

Everything here is read from what was recorded as it happened: the engine's events (started, each step, stopping,
ended), the audited commands that carried the operation's id, and the alarms raised or cleared in the run's time.
Nothing is reconstructed or estimated: a run with no end event is shown as not ended (the service stopped), and a
command whose outcome is unknown is shown as unknown. The program version is the one the run used.
"""
from __future__ import annotations

import json

RUN_KINDS = ("PROGRAM_OP_STARTED", "PROGRAM_STEP", "PROGRAM_OP_STOPPING", "PROGRAM_GUARD_STOP", "PROGRAM_STOP_UNVERIFIED",
             "PROGRAM_OP_ENDED", "PROGRAM_PUMP_FAILED", "PROGRAM_PUMP_REPLACED", "PROGRAM_PUMP_REPLACEMENT_BLOCKED",
             "PROGRAM_PUMP_WAITING", "PROGRAM_RESUMED")
ALARM_KINDS = ("ALARM_RAISED", "ALARM_CLEARED", "ALARM_ACKNOWLEDGED", "PROGRAM_ALARM_RAISED", "PROGRAM_ALARM_CLEARED",
               "TWIN_ALARM_RAISED", "TWIN_ALARM_CLEARED", "EQUIPMENT_FAULT", "EQUIPMENT_FAULT_CLEARED")


def _events(hist, kinds, start=None, end=None, limit=5000):
    sql = f"SELECT id, utc, kind, actor, payload_json FROM events WHERE kind IN ({','.join('?' * len(kinds))})"
    args: list = list(kinds)
    if start:
        sql += " AND utc>=?"
        args.append(start)
    if end:
        sql += " AND utc<=?"
        args.append(end)
    rows = hist.query(sql + " ORDER BY id DESC LIMIT ?", tuple(args + [limit]))
    return [dict(r) | {"payload": json.loads(r["payload_json"])} for r in rows]


def list_runs(hist, *, start=None, end=None, program=None, result=None, limit=100) -> list[dict]:
    started = _events(hist, ("PROGRAM_OP_STARTED",), start, end, limit=min(5000, limit * 5))
    ended = {e["payload"].get("op_id"): e for e in _events(hist, ("PROGRAM_OP_ENDED",), start, None, limit=20000)}
    out = []
    for s in started:
        p = s["payload"]
        if program and p.get("program") != program:
            continue
        e = ended.get(p.get("op_id"))
        ep = e["payload"] if e else {}
        res = ep.get("result") if e else "NOT ENDED"
        if result and res != result:
            continue
        out.append({"op_id": p.get("op_id"), "program_id": p.get("program"), "version": p.get("version"),
                    "origin": p.get("origin"), "actor": s["actor"], "occurrence": p.get("key"),
                    "started_utc": s["utc"], "ended_utc": e["utc"] if e else None,
                    "sim_started_utc": p.get("sim_utc"), "sim_ended_utc": ep.get("sim_utc"),
                    "result": res, "reason": ep.get("reason"), "runtime_s": ep.get("runtime_s"),
                    "measured_L": ep.get("measured_L"), "uncertain_L": ep.get("uncertain_L"),
                    "requested_L": ep.get("requested_L"), "runtime": p.get("runtime"), "lead": p.get("lead"),
                    "context": p.get("context")})
        if len(out) >= limit:
            break
    return out


def run_detail(hist, op_id: str, programs=None) -> dict | None:
    runs = [r for r in list_runs(hist, limit=5000) if r["op_id"] == op_id]
    if not runs:
        return None
    run = runs[0]
    end = run["ended_utc"] or "9999"
    timeline = []
    for e in reversed(_events(hist, RUN_KINDS + ALARM_KINDS, run["started_utc"], end, limit=2000)):   # recorded order
        p = e["payload"]
        if e["kind"] in RUN_KINDS and p.get("op_id") not in (None, op_id):
            continue
        timeline.append({"utc": e["utc"], "kind": e["kind"], "actor": e["actor"],
                         "what": _words(e["kind"], p), "sim_utc": p.get("sim_utc")})
        if e["kind"] == "PROGRAM_OP_STARTED" and p.get("op_id") == op_id:
            timeline = timeline[-1:]            # recorded before the start in the same instant: not part of this run
    cmds = hist.query("SELECT intent_utc, target, desired, outcome, detail, readback, actor FROM commands "
                      "WHERE reason LIKE ? ORDER BY intent_utc LIMIT 500", (f"%{op_id}%",))
    for c in cmds:
        timeline.append({"utc": c["intent_utc"], "kind": "COMMAND", "actor": c["actor"],
                         "what": f"{c['target']} {'ON' if c['desired'] else 'OFF'}: {c['outcome']}"
                                 + (f" ({c['detail']})" if c["detail"] else "")})
    timeline.sort(key=lambda x: x["utc"])           # stable: events that share a time keep their recorded order
    body = None
    if programs is not None and run["program_id"] and run["version"]:
        try:
            body = programs.get(run["program_id"], run["version"])["body"]
        except Exception:
            body = None
    conf = configuration(hist, run["started_utc"], body, run.get("context")) if body else None
    if conf is not None:
        conf["lead"] = run.get("lead")
        conf["stop_plan"] = next((e["what"] for e in timeline if e["kind"] == "PROGRAM_OP_STOPPING"), None)
    return run | {"timeline": timeline, "program_body": body, "configuration": conf,
                  "chart": {"points": [b | {"label": f"{b['name']} {b['role']}"} for b in (conf or {}).get("bindings", [])
                                       if b["point"]],
                            "start": run["started_utc"], "end": run["ended_utc"],
                            "meaning": "the channels bound to this run's equipment when it started; gaps and bad "
                                       "quality are shown as recorded, never filled"}}


def configuration(hist, at_utc: str, body: dict, held: dict | None = None) -> dict:
    """HIST-03 (0.7.3): the configuration the run executed with, rebuilt from the versioned records as they were
    when it started: the block version the program was compiled over, each item's version and type version, and
    each channel binding in force. Later edits do not change it; what changed since is listed beside it."""
    items, bindings, changed = [], [], []
    from ..twin.changes import program_items
    held_items = (held or {}).get("items") or {}
    for iid in sorted(program_items(body)):
        h = held_items.get(iid)
        if h:                                      # C075-05: the record the engine held at the start, by its digest
            then = hist.query("SELECT item_id, version, status, type_id, type_version, name, digest, json FROM "
                              "equipment_items WHERE item_id=? AND digest=? ORDER BY version DESC LIMIT 1", (iid, h["body"]))
        else:                                      # runs recorded before 0.7.6: rebuilt from record times
            then = hist.query("SELECT item_id, version, status, type_id, type_version, name, digest, json FROM "
                              "equipment_items WHERE item_id=? AND created_utc<=? ORDER BY version DESC LIMIT 1",
                              (iid, at_utc))
        now = hist.query("SELECT version, name, digest FROM equipment_items WHERE item_id=? ORDER BY version DESC LIMIT 1",
                         (iid,))
        if not then:
            items.append({"item_id": iid, "version": None, "why": "no record of this item before the run"})
            continue
        t = dict(then[0])
        ib = json.loads(t.pop("json"))
        items.append(t | {"scaling": ib.get("scaling") or {}, "properties": ib.get("properties") or {}})
        if now and now[0]["digest"] != t["digest"]:
            changed.append(f"{t['name']}: edited after this run (v{t['version']} then, v{now[0]['version']} now)")
        roles = sorted(set(h["points"]) if h else {r for (r,) in hist.query(
            "SELECT DISTINCT role FROM channel_bindings WHERE item_id=?", (iid,))})
        for role in roles:
            if h:
                b = [{"point": h["points"][role], "status": "AS RUN", "utc": at_utc}]
            else:
                b = hist.query("SELECT point, status, utc FROM channel_bindings WHERE item_id=? AND role=? AND utc<=? "
                               "ORDER BY seq DESC LIMIT 1", (iid, role, at_utc))
            cur = hist.query("SELECT point, status FROM channel_bindings WHERE item_id=? AND role=? ORDER BY seq DESC LIMIT 1",
                             (iid, role))
            then_b = dict(b[0]) if b else {"point": None, "status": "NOT BOUND", "utc": None}
            bindings.append({"item_id": iid, "name": t["name"], "role": role} | then_b)
            if cur and (cur[0]["point"] != then_b["point"] or not h and cur[0]["status"] != then_b["status"]):
                changed.append(f"{t['name']} {role}: {then_b['point'] or 'no channel'} ({then_b['status']}) then, "
                               f"{cur[0]['point'] or 'no channel'} ({cur[0]['status']}) now")
    block = None
    a = body.get("authoring") or {}
    if a.get("block_id") and a.get("block_version"):
        r = hist.query("SELECT block_id, version, status, name, digest, topology_rev, json FROM blocks WHERE block_id=? "
                       "AND version=?", (a["block_id"], a["block_version"]))
        if r:
            block = dict(r[0])
            bb = json.loads(block.pop("json"))
            block |= {"members": bb.get("members", []), "safety": bb.get("safety"), "duty": bb.get("duty")}
            latest = hist.query("SELECT max(version) AS v FROM blocks WHERE block_id=? AND status='VALIDATED'", (a["block_id"],))
            if latest and latest[0]["v"] and latest[0]["v"] != block["version"]:
                changed.append(f"block {block['name']}: v{block['version']} then, v{latest[0]['v']} validated now")
    topo = hist.query("SELECT rev, digest FROM topology_revisions WHERE utc<=? ORDER BY utc DESC, rev DESC LIMIT 1", (at_utc,))
    return {"at_utc": at_utc, "block": block, "items": items, "bindings": bindings,
            "topology": dict(topo[0]) if topo else None, "changed_since": changed,
            "meaning": "rebuilt from the versioned records in force when the run started; later edits are listed, "
                       "never applied to this view"}


# owner, 28 Oct 2026: every program alarm in the operator's words, with what to do
PROGRAM_ALARM_WORDS = {
    "PROGRAM_STOP_UNVERIFIED": ("a stop is not verified{detail}", "the controller keeps sending OFF; check at the "
                                "equipment that it really stopped, and switch it off at the panel if it stays on"),
    "PROGRAM_OP_FAILED": ("the program stopped or could not start{detail}", "fix the cause named here, then run it again"),
    "PROGRAM_RUNTIME_GUARD": ("the program stopped at its safety limit (maximum running time or volume)",
                              "check that water flows as expected and that the limit fits the program"),
    "PROGRAM_GUARD": ("a stop condition could not be read, so the program stopped for safety{detail}",
                      "check that sensor (Equipment shows its reading)"),
    "PROGRAM_PUMP_FAILED": ("a pump failed{detail}", "the other pump of the pair takes over; check the failed pump"),
    "PROGRAM_CONFLICT": ("two programs need the same equipment", "choose on Operation which one continues"),
    "EQUIPMENT_FAULT": ("equipment reports a fault{detail}", "find and clear the fault at the equipment; nothing starts "
                        "on it until it clears"),
    "RECOVERY_APPROVAL_REQUIRED": ("the controller restarted", "nothing runs until the restart checklist is approved on "
                                   "Operation"),
}


def _alarm_words(p: dict, active: bool) -> str:
    rule = p.get("rule") or p.get("rule_id") or ""
    what, fix = PROGRAM_ALARM_WORDS.get(rule, (rule.replace("_", " ").lower() or "an alarm", "see the details"))
    d = p.get("why") or p.get("message") or p.get("item") or ""
    if isinstance(p.get("items"), list) and p["items"]:
        d = "; ".join(map(str, p["items"]))
    what = what.format(detail=f": {d}" if d else "")
    return f"{what}. What to do: {fix}." if active else f"cleared: {what.split(':')[0]}"


def _words(kind: str, p: dict) -> str:
    if kind in ("PROGRAM_ALARM_RAISED", "PROGRAM_ALARM_CLEARED"):
        return _alarm_words(p, kind.endswith("RAISED"))
    if kind == "PROGRAM_OP_STARTED":
        return f"started ({p.get('origin', '').lower()}), limit {p.get('limit_s')} s"
    if kind == "PROGRAM_STEP":
        return f"step {p.get('step')}"
    if kind == "PROGRAM_OP_STOPPING":
        return f"stopping: {p.get('why')} ({p.get('result')})"
    if kind == "PROGRAM_OP_ENDED":
        return f"ended {p.get('result')}: {p.get('reason') or ''}".rstrip(": ")
    if kind == "PROGRAM_PUMP_FAILED":
        return f"{p.get('failed')} failed ({p.get('why')}); {p.get('standby')} takes over this cycle"
    if kind == "PROGRAM_PUMP_REPLACED":
        w = p.get("watered_s")
        return (f"{p.get('by')} replaced {p.get('failed')} after {p.get('changeover_s')} s"
                + (f"; {w:.0f} s had been watered, the rest continues" if w is not None else ""))
    if kind == "PROGRAM_PUMP_WAITING":
        rem = p.get("remaining")
        return (f"{p.get('failed')} failed; {p.get('partner')} is serving the other sector: this sector waits for it"
                + (f" ({rem:g} left)" if rem is not None else ""))
    if kind == "PROGRAM_RESUMED":
        return f"the rest of the cycle from {p.get('from_op')} runs now on {p.get('lead') or 'the healthy pump'}"
    if kind == "PROGRAM_PUMP_REPLACEMENT_BLOCKED":
        return f"{p.get('standby')} could not replace {p.get('failed')}: {p.get('blocked')}"
    if kind in ("PROGRAM_GUARD_STOP", "PROGRAM_STOP_UNVERIFIED"):
        return str(p.get("why") or p.get("items"))
    return str(p.get("message") or p.get("rule") or p.get("rule_id") or p)[:200]


CSV_COLUMNS = ("op_id", "program_id", "version", "origin", "actor", "occurrence", "started_utc", "ended_utc",
               "result", "reason", "runtime_s", "measured_L", "uncertain_L", "requested_L")
