"""HTTP routes of Operation History's runs (0.7.0), in the live app and the twin app (each over its own database).
Operators and administrators (OPS_READ); CSV escapes text against spreadsheet formulas."""
from __future__ import annotations

import csv
import io

from fastapi import Depends, HTTPException, Query
from fastapi.responses import Response

from ..historian.runs import CSV_COLUMNS, list_runs, run_detail
from ..security.auth import Perm, Principal


def _esc(v):
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def register(app, require, hist, programs=None) -> None:
    @app.get("/api/history/runs")
    def runs(p: Principal = Depends(require(Perm.OPS_READ)), start: str | None = None, end: str | None = None,
             program: str | None = Query(None, max_length=48), result: str | None = Query(None, max_length=32),
             limit: int = Query(100, ge=1, le=1000)):
        return {"runs": list_runs(hist, start=start, end=end, program=program, result=result, limit=limit),
                "meaning": "Recorded as it happened: engine events, audited commands and alarms. A run without an end "
                           "was interrupted (the service stopped); nothing is estimated."}

    @app.get("/api/history/runs.csv")
    def runs_csv(p: Principal = Depends(require(Perm.OPS_READ)), start: str | None = None, end: str | None = None,
                 program: str | None = Query(None, max_length=48), limit: int = Query(1000, ge=1, le=10000)):
        rows = list_runs(hist, start=start, end=end, program=program, limit=limit)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(CSV_COLUMNS)
        for r in rows:
            w.writerow([_esc(r.get(k)) for k in CSV_COLUMNS])
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="aquacrop-runs.csv"'})

    @app.get("/api/history/runs/{op_id}")
    def run(op_id: str, p: Principal = Depends(require(Perm.OPS_READ))):
        d = run_detail(hist, op_id, programs)
        if d is None:
            raise HTTPException(404, "no such run")
        return d
