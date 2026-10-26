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
             "PROGRAM_OP_ENDED")
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
                    "requested_L": ep.get("requested_L"), "runtime": p.get("runtime")})
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
    return run | {"timeline": timeline, "program_body": body}


def _words(kind: str, p: dict) -> str:
    if kind == "PROGRAM_OP_STARTED":
        return f"started ({p.get('origin', '').lower()}), limit {p.get('limit_s')} s"
    if kind == "PROGRAM_STEP":
        return f"step {p.get('step')}"
    if kind == "PROGRAM_OP_STOPPING":
        return f"stopping: {p.get('why')} ({p.get('result')})"
    if kind == "PROGRAM_OP_ENDED":
        return f"ended {p.get('result')}: {p.get('reason') or ''}".rstrip(": ")
    if kind in ("PROGRAM_GUARD_STOP", "PROGRAM_STOP_UNVERIFIED"):
        return str(p.get("why") or p.get("items"))
    return str(p.get("message") or p.get("rule") or p.get("rule_id") or p)[:200]


CSV_COLUMNS = ("op_id", "program_id", "version", "origin", "actor", "occurrence", "started_utc", "ended_utc",
               "result", "reason", "runtime_s", "measured_L", "uncertain_L", "requested_L")
