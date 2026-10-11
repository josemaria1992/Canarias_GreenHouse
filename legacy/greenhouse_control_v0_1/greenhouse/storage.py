"""Append-only raw snapshots plus events, setpoints and alarms in local SQLite."""
import csv
import io
import json
import sqlite3
import threading
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Storage:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        with self.lock, self.db:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA busy_timeout=10000")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS samples (
                   id INTEGER PRIMARY KEY, ts TEXT NOT NULL, analog_json TEXT NOT NULL,
                   analog_types_json TEXT NOT NULL, inputs_json TEXT NOT NULL,
                   relays_json TEXT NOT NULL, mode TEXT NOT NULL,
                   sector INTEGER, auto_phase TEXT NOT NULL, quality TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS samples_ts ON samples(ts);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, ts TEXT NOT NULL, type TEXT NOT NULL,
                    actor TEXT NOT NULL, detail TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY, value_json TEXT NOT NULL
                );
            """)

    def sample(self, snapshot, mode, sector, phase, quality):
        with self.lock, self.db:
            self.db.execute("""INSERT INTO samples
             (ts, analog_json, analog_types_json, inputs_json, relays_json,
              mode, sector, auto_phase, quality)
             VALUES (?,?,?,?,?,?,?,?,?)""",
             (now(), json.dumps(snapshot.analog if snapshot else None),
              json.dumps(snapshot.analog_types if snapshot else None),
              json.dumps(snapshot.digital_inputs if snapshot else None),
              json.dumps(snapshot.relays if snapshot else None),
              mode, sector, phase, quality))

    def event(self, kind, actor, detail):
        with self.lock, self.db:
            self.db.execute("INSERT INTO events(ts,type,actor,detail) VALUES (?,?,?,?)",
                            (now(), kind, actor, detail))

    def settings_get(self, key, default):
        with self.lock:
            row = self.db.execute("SELECT value_json FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value_json"]) if row else default

    def settings_set(self, key, value):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO settings(key,value_json) VALUES (?,?)",
                            (key, json.dumps(value)))

    def events(self, limit=100):
        with self.lock:
            rows = self.db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?",
                                   (limit,)).fetchall()
        return [dict(r) for r in rows]

    def samples(self, limit=500):
        with self.lock:
            rows = self.db.execute("SELECT * FROM samples ORDER BY id DESC LIMIT ?",
                                   (limit,)).fetchall()
        return [{**dict(r), **{k: json.loads(r[k]) for k in
               ("analog_json", "analog_types_json", "inputs_json", "relays_json")}}
                for r in reversed(rows)]

    def export_csv(self, limit=20000):
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["utc", "mode", "sector", "auto_phase", "quality"] +
                        [f"AI_{i}_raw" for i in range(1, 9)] +
                        [f"AI_{i}_type" for i in range(1, 9)] +
                        [f"DI_{i}" for i in range(1, 9)] +
                        [f"DO_{i}" for i in range(1, 9)])
        for r in self.samples(limit):
            def packed(col):
                seq = r[col]
                return seq if seq is not None else [""] * 8
            writer.writerow([r["ts"], r["mode"], r["sector"], r["auto_phase"], r["quality"]] +
                            packed("analog_json") + packed("analog_types_json") +
                            packed("inputs_json") + packed("relays_json"))
        return output.getvalue()

    def close(self):
        with self.lock:
            self.db.close()
