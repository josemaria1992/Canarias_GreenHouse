"""Durable stores for process recipes and stock readiness (schema v3; ADR-0014).

Both are operator records: every save is written synchronously, so an acknowledged change survives a
restart. The recipe activation log and the stock readiness history are append-only.
"""
from __future__ import annotations

import json

from ..control.recipes import Recipe, RecipeRecord
from .schema import utc_iso


class HistorianRecipeBackend:
    def __init__(self, hist):
        self.hist = hist

    def load(self) -> tuple[list[RecipeRecord], list[dict]]:
        recs = []
        for (j,) in self.hist.read(lambda c: c.execute("SELECT json FROM recipes").fetchall()):
            d = json.loads(j)
            d["recipe"] = Recipe.model_validate(d["recipe"])
            recs.append(RecipeRecord(**d))
        acts = [json.loads(j) for (j,) in self.hist.read(
            lambda c: c.execute("SELECT json FROM recipe_activations ORDER BY seq").fetchall())]
        return recs, acts

    def save(self, rec: RecipeRecord) -> None:
        r = rec.recipe
        doc = {"recipe": json.loads(r.model_dump_json(by_alias=True)), "status": rec.status,
               "created_utc": rec.created_utc, "created_by": rec.created_by, "validated_utc": rec.validated_utc,
               "activated_utc": rec.activated_utc, "activated_by": rec.activated_by, "issues": rec.issues}
        row = (r.recipe_id, r.version, rec.status, r.provenance, r.name, r.digest(), rec.created_utc, rec.created_by,
               utc_iso(), json.dumps(doc))
        self.hist.submit(lambda c: c.execute("INSERT OR REPLACE INTO recipes VALUES(?,?,?,?,?,?,?,?,?,?)", row),
                         wait=True)

    def activation(self, entry: dict) -> None:
        row = (entry["seq"], entry["utc"], entry["actor"], entry["recipe"], entry["digest"], entry["action"],
               json.dumps(entry))
        self.hist.submit(lambda c: c.execute("INSERT INTO recipe_activations VALUES(?,?,?,?,?,?,?)", row), wait=True)


class HistorianReadinessBackend:
    def __init__(self, hist):
        self.hist = hist

    def load_stock_readiness(self) -> list[dict]:
        """The newest entry per stock (the history stays in the table)."""
        rows = self.hist.read(lambda c: c.execute(
            "SELECT json FROM stock_readiness WHERE seq IN (SELECT max(seq) FROM stock_readiness GROUP BY stock)"
        ).fetchall())
        return [json.loads(j) for (j,) in rows]

    def save_stock_readiness(self, entry: dict) -> None:
        row = (entry["seq"], entry["stock"], entry["status"], entry.get("actor"), entry.get("utc"), json.dumps(entry))
        self.hist.submit(lambda c: c.execute("INSERT INTO stock_readiness VALUES(?,?,?,?,?,?)", row), wait=True)
