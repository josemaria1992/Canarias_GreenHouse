"""Time a year-range chart as `/api/trends` draws it (OP-03): a first read with an empty `TrendCache`,
then a refresh that reuses the final buckets, both compared with an uncached read.

Runs against a scratch database built by measure_storage.py (it opens a historian session on it;
never point it at an installation's database):

    python scripts/measure_trend_cache.py --db /tmp/m/measure.sqlite3 --out /tmp/m/trend-cache.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from measure_storage import config, environment  # noqa: E402

from aquacrop.domain.model import new_id  # noqa: E402
from aquacrop.historian.trends import TrendCache, trend_buckets, trend_level  # noqa: E402
from aquacrop.historian.writer import Historian  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--inventory", type=int, default=48, choices=(24, 48))
    ap.add_argument("--points", type=int, default=16)
    a = ap.parse_args()
    out = Path(a.out)
    cfg = config(a.inventory)
    rep: dict = {"environment": environment(out.parent, 0), "parameters": vars(a)}
    h = Historian(Path(a.db), cfg, new_id("sess"), backend_name="MODBUS_RTU")
    try:
        end = datetime.fromisoformat(h.query("SELECT max(acquired_utc) AS m FROM samples")[0]["m"])
        now = end + timedelta(seconds=30)     # the newest sample is 30 s old, as on a running controller
        s, e = (end - timedelta(days=365)).isoformat(), end.isoformat()
        ids = [str(p) for p in cfg.all_points()][:a.points]
        level, cache, ref = trend_level(365 * 86400, 2000), TrendCache(), None
        for name, c in (("uncached", None), ("first_read_filling_the_cache", cache), ("refresh_from_the_cache", cache)):
            tic = time.perf_counter()
            rows = [trend_buckets(h, pid, s, e, level, cache=c, now=now) for pid in ids]
            rep[name] = {"seconds": round(time.perf_counter() - tic, 3), "buckets": sum(len(r) for r in rows),
                         "samples": sum(x[13] for r in rows for x in r)}
            if ref is None:
                ref = rows
            rep[name]["equals_uncached"] = rows == ref
        rep.update(range={"start": s, "end": e}, bucket=level[0], points=ids)
    finally:
        h.close()
    out.write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in rep.items() if isinstance(v, dict) and "seconds" in v}))
    return 0 if all(rep[k]["equals_uncached"] for k in ("first_read_filling_the_cache", "refresh_from_the_cache")) else 1


if __name__ == "__main__":
    sys.exit(main())
