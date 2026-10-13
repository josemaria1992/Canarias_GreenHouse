"""Measure the event and command workload of the process controllers (OP-06), for measure_storage.py.

Runs the SIMULATED installation (installation-48 twin, DEMO recipe, synthetic values) with irrigation,
return, refill and acid all in AUTO for --hours of simulated time, and counts what the server would
record: events by kind (with their JSON payload sizes and a few sample payloads) and commands (durable
intent plus outcome). Irrigation alternates S1/MP1 and S2/MP2 for 300 s each without a break, so the
rates are an upper bound for a plant that irrigates around the clock; a real schedule with pauses
writes less. Nothing here touches a real installation, and no rate here is a production setting.

    python scripts/measure_process_workload.py --hours 48 --out workload.json
    python scripts/measure_storage.py --days 400 --process-workload workload.json --out OUT
"""
from __future__ import annotations

import argparse
import collections
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import aquacrop
from aquacrop.cli import resolve_config_path
from aquacrop.config import load_config
from aquacrop.twin.installation_scenarios import tw, with_twin_overrides
from aquacrop.twin.runtime import make_runtime
from aquacrop.twin.scenarios import SCN

SAMPLES_PER_KIND = 20
DT_S = 0.5     # the coarser tick of the long process scenarios; the control period stays 0.5 s


class CountingAudit:
    """The runtime's audit sink: counts instead of storing, keeps a few payloads per event kind."""

    def __init__(self) -> None:
        self.events: collections.Counter = collections.Counter()
        self.payload_bytes: collections.Counter = collections.Counter()
        self.samples: dict[str, list] = collections.defaultdict(list)
        self.intents = 0
        self.outcomes = 0
        self.by_origin: collections.Counter = collections.Counter()

    def command_intent(self, rec) -> None:
        self.intents += 1
        self.by_origin[str(getattr(rec.command.origin, "value", rec.command.origin))] += 1

    def command_outcome(self, rec) -> None:
        self.outcomes += 1

    def event(self, kind, payload, *, actor=None, sim_s=None) -> None:
        self.events[kind] += 1
        self.payload_bytes[kind] += len(json.dumps(payload, default=str))
        if len(self.samples[kind]) < SAMPLES_PER_KIND:
            self.samples[kind].append({"actor": actor, "payload": json.loads(json.dumps(payload, default=str))})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hours", type=float, default=48.0)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--config", default="installation-48")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    cfg = with_twin_overrides(load_config(resolve_config_path(a.config)), tw(None, DT_S))
    audit = CountingAudit()
    rt = make_runtime(cfg, seed=a.seed, audit=audit)
    for s in ("A", "B", "ACID"):
        rt.schedule(0, "STOCK_ACK", SCN, {"stock": s, "note": "measurement: synthetic stocks prepared"})
    for p in ("IRRIGATION", "RETURN", "REFILL", "ACID"):
        rt.schedule(1, "PROCESS_REQUEST", SCN, {"process": p, "target": "AUTO"})
    tic = time.perf_counter()
    rt.run_for(a.hours * 3600)
    days = a.hours / 24
    sup = rt.sup.by_id
    kinds = {k: {"count": n, "per_day": round(n / days, 1), "mean_payload_bytes": round(audit.payload_bytes[k] / n),
                 "samples": audit.samples[k]} for k, n in audit.events.most_common()}
    rep = {"what": "process event and command workload of the SIMULATED installation (all processes AUTO); "
                   "synthetic values, not a production setting and not a measurement of a real plant",
           "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "aquacrop": aquacrop.__version__,
           "config": cfg.config_id, "seed": a.seed, "simulated_hours": a.hours, "dt_s": DT_S,
           "wall_seconds": round(time.perf_counter() - tic, 1),
           "states": {p: sup[p].state.value for p in ("IRRIGATION", "RETURN", "REFILL", "ACID")},
           "activity": {"irrigation_steps": audit.events["IRRIGATION_STEP_COMPLETED"],
                        "return_runs": audit.events["RETURN_RUN_ENDED"],
                        "refill_batches": len(getattr(sup["REFILL"], "batches", [])),
                        "acid_doses": audit.events["ACID_DOSE_ENDED"]},
           "events_per_day": round(sum(audit.events.values()) / days, 1),
           "commands_per_day": round(audit.intents / days, 1), "command_outcomes": audit.outcomes,
           "commands_by_origin": dict(audit.by_origin), "kinds": kinds}
    a.out.write_text(json.dumps(rep, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in rep.items() if k != "kinds"} |
                     {"kinds": {k: (v["per_day"], v["mean_payload_bytes"]) for k, v in kinds.items()}}, indent=1))


if __name__ == "__main__":
    main()
