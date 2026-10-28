"""Editable public IDs over stable keys (0.7.1, owner decision 26 October 2026).

Equipment items and programs keep the key they were created with (`item_id`, `program_id`): every graph node, block
member, channel binding, program body, schedule occurrence, alarm, usage record, journal step and history event names
that key, and none of them is rewritten. What the owner sees and edits as "ID" is the public ID: by default the key
itself (every existing lower-case ID stays exactly as it is), or the ID the owner typed, in the case they typed it.

* A new item's or program's key is derived from the ID typed (`Valve_1` -> `valve_1`, made unique), so references
  keep the existing lower-case rules and nothing downstream changes.
* Renaming changes the public ID only: one row and one log line, under a revision check. It changes no executable
  meaning (proof contexts name keys, never IDs or names), so validated program versions, twin proof, held channels of
  a running operation and history all stay valid and resolvable; the log keeps every earlier ID.
* Public IDs are unique per kind, ignoring case (`Valve_1` and `valve_1` would confuse people and history), across
  retired objects too. The unique index makes two concurrent renames to the same ID impossible.
"""
from __future__ import annotations

import re
import threading
import unicodedata
from datetime import datetime, timezone

PUBLIC_ID_RE = r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,47}$"
KEY_RE = r"^[a-z0-9][a-z0-9_\-]{0,47}$"
_PUBLIC = re.compile(PUBLIC_ID_RE)
_KEY = re.compile(KEY_RE)
PUBLIC_RULE = "1-48 letters (any case), digits, - or _, starting with a letter or digit"


class IdRefused(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def id_from_name(name: str) -> str:
    """The suggested ID for a name: spaces removed, a trailing number set apart by an underscore, the case kept.
    `Valve 1` -> `Valve_1`; `Irrigation Pump 2` -> `IrrigationPump_2`. Accents are dropped, other signs removed."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode("ascii").strip()
    m = re.match(r"^(.*?)[\s_\-]*(\d+)$", s)
    base, num = (m.group(1), m.group(2)) if m else (s, "")
    base = re.sub(r"[^A-Za-z0-9_\-]", "", re.sub(r"\s+", "", base)).strip("_-")
    out = f"{base}_{num}" if base and num else base or num
    out = out.lstrip("_-")[:48]
    return out


def check_public(public_id: str) -> str:
    if not isinstance(public_id, str) or not _PUBLIC.match(public_id):
        raise IdRefused(f"ID {public_id!r}: use {PUBLIC_RULE}")
    return public_id


def key_for(public_id: str, taken: set[str]) -> str:
    """A stable key for a new object: the ID in lower case, made unique among `taken` keys."""
    base = re.sub(r"[^a-z0-9_\-]", "-", public_id.lower()).strip("-_")[:44] or "item"
    if not base[0].isalnum():
        base = "x" + base[:43]
    key, n = base, 2
    while key in taken:
        key, n = f"{base}-{n}", n + 1
    assert _KEY.match(key)
    return key


class PublicIds:
    """The `public_ids` table for one kind ("item" or "program")."""

    def __init__(self, hist, kind: str):
        self.h, self.kind = hist, kind
        self._lock = threading.RLock()

    def all(self) -> dict[str, dict]:
        rows = self.h.query("SELECT key, public_id, rev, actor, utc FROM public_ids WHERE kind=?", (self.kind,))
        return {r["key"]: dict(r) for r in rows}

    def public(self, key: str) -> str:
        r = self.all().get(key)
        return r["public_id"] if r else key

    def rev(self, key: str) -> int:
        r = self.all().get(key)
        return r["rev"] if r else 0

    def history(self, key: str) -> list[dict]:
        return [dict(r) for r in self.h.query("SELECT old_id, new_id, rev, actor, utc FROM public_id_log WHERE kind=? "
                                              "AND key=? ORDER BY seq", (self.kind, key))]

    def resolve(self, given: str, keys: set[str]) -> str | None:
        """The key that a public ID (any case) names, else the key given itself, else None. A public ID wins over
        a key, since a renamed object's old key may equal another object's ID."""
        low = (given or "").lower()
        rows = self.all()
        for k in keys:
            if (rows[k]["public_id"] if k in rows else k).lower() == low:
                return k
        return given if given in keys else None

    def _clash(self, public_id: str, key: str, keys: set[str]) -> str | None:
        low = public_id.lower()
        rows = self.all()
        for k in keys:
            if k == key:
                continue
            if (rows[k]["public_id"] if k in rows else k).lower() == low:
                return k
        return None

    def claim(self, key: str, public_id: str, keys: set[str], actor: str) -> None:
        """Record the public ID of a new object (no row is needed when it equals the key)."""
        check_public(public_id)
        with self._lock:
            other = self._clash(public_id, key, keys)
            if other is not None:
                raise IdRefused(f"ID {public_id} is already used by {self.public(other)}")
            if public_id == key:
                return
            self._write(key, None, public_id, 1, actor)

    def rename(self, key: str, new_id: str, expected_rev: int, keys: set[str], actor: str) -> dict:
        check_public(new_id)
        with self._lock:
            cur = self.all().get(key)
            rev = cur["rev"] if cur else 0
            if rev != expected_rev:
                raise IdRefused(f"the ID was changed meanwhile (now {self.public(key)}); reload and edit again")
            old = cur["public_id"] if cur else key
            if new_id == old:
                return {"key": key, "public_id": old, "rev": rev, "unchanged": True}
            other = self._clash(new_id, key, keys)
            if other is not None:
                raise IdRefused(f"ID {new_id} is already used by {self.public(other)}")
            self._write(key, old, new_id, rev + 1, actor)
            return {"key": key, "public_id": new_id, "rev": rev + 1, "previous": old}

    def _write(self, key: str, old: str | None, new: str, rev: int, actor: str) -> None:
        """One transaction checks and writes: no other row of this kind has the ID (any case) and the revision is
        still the one read, so a concurrent rename either wins or is refused, never both. Nothing raises inside the
        historian's batch (which would roll back unrelated writes queued with it)."""
        now = _now()

        def tx(c):
            if c.execute("SELECT 1 FROM public_ids WHERE kind=? AND key<>? AND lower(public_id)=lower(?)",
                         (self.kind, key, new)).fetchone():
                return "clash"
            row = c.execute("SELECT rev FROM public_ids WHERE kind=? AND key=?", (self.kind, key)).fetchone()
            if (row[0] if row else 0) != rev - 1:
                return "stale"
            c.execute("INSERT INTO public_ids(kind, key, public_id, rev, actor, utc) VALUES(?,?,?,?,?,?) "
                      "ON CONFLICT(kind, key) DO UPDATE SET public_id=excluded.public_id, rev=excluded.rev, "
                      "actor=excluded.actor, utc=excluded.utc", (self.kind, key, new, rev, actor, now))
            c.execute("INSERT INTO public_id_log(kind, key, old_id, new_id, rev, actor, utc) VALUES(?,?,?,?,?,?,?)",
                      (self.kind, key, old, new, rev, actor, now))
            return "ok"
        res = self.h.submit(tx, wait=True)
        if res == "clash":
            raise IdRefused(f"ID {new} is already used")
        if res == "stale":
            raise IdRefused(f"the ID was changed meanwhile (now {self.public(key)}); reload and edit again")
        self.h.event("PUBLIC_ID_CHANGED", {"kind": self.kind, "key": key, "from": old, "to": new, "rev": rev}, actor=actor)
