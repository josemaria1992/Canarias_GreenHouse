"""SD-card daily backups and encrypted OneDrive copies (owner request of 2026-10-12, ADR-0012).

The working database stays on the eMMC; recording (60 s snapshots, 365-day window) is untouched by
everything here. Backup, upload, prune and drill run as separate short processes (systemd timers or
the CLI), never inside the recording path, so a missing card, a slow card or no internet cannot stall
recording. The server only reads the state files below and raises alarms from them.

    <state>/offsite/
      queue/<backup>.json     uploads still to do (persist across restarts; retried with backoff)
      ledger.json             copies verified in the cloud (cryptcheck), used by the drill and pruning
      status.json            last card check, backup, upload, listing, plan and drill (for Settings)
      journal.jsonl          append-only record of every outcome; the server copies it into events
      policy-confirmed.json  the copy policy an administrator confirmed (pruning runs only if it matches)
      drills/                restore-drill reports
      offsite.lock           one offsite operation at a time

Daily backup: verify the card's identity (sdcard.py) -> consistent compressed backup with checksum and
manifest written to the card and verified before publication (backup.create_backup; a failure leaves
the earlier backups untouched) -> read back from the card and verified again -> queued for upload.
Upload: rclone `copyto` of the backup and its manifest to the crypt remote, then `cryptcheck`; only a
verified copy enters the ledger. Failures stay queued with exponential backoff; a success after a
failure drains the rest of the queue at once (connectivity is back).

Pruning never runs until an administrator confirmed the configured policy (7 local daily; 30 daily +
12 monthly in the cloud, proposed). It never removes a backup still waiting for upload, a copy that is
not in the ledger, or anything when backup names are later than the clock.

Monthly drill: download the newest verified cloud copy (rclone decrypts it) into a fresh directory
on the verified card, check its checksum against the ledger and manifest, then the full restore check
(integrity, foreign keys, schema, counts, fingerprints, application smoke test) in that scratch space.
Sessions and bench leases in the restored copy are deleted before the smoke test, so nothing restored
can carry a login or hardware permission; the scratch directory is removed afterwards. The production
database is only ever read.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from .backup import (BackupAborted, BackupFailed, BackupRefused, _sha256, _write_atomic, create_backup,
                     estimate_backup_bytes, list_backups, manifest_path, verify_backup)
from .rclone import Rclone, RcloneError
from .sdcard import Card, VolumeError, VolumeProbe, on_card, verify_card

JOURNAL_MAX_BYTES = 1024 * 1024
STOP_ON = {"OFFLINE", "AUTHORIZATION", "NOT_ENCRYPTED", "NOT_CONFIGURED", "RCLONE_MISSING", "TIMEOUT", "CLOUD_FULL",
           "SD_CARD_MISSING", "SD_CARD_WRONG", "SD_CARD_READ_ONLY", "SD_CARD_NOT_CONFIGURED"}
FUTURE_SLACK_S = 3600.0


class NotActivated(RuntimeError):
    pass


class Busy(RuntimeError):
    pass


class PolicyMismatch(RuntimeError):
    def __init__(self, policy: dict):
        super().__init__("the copy policy changed since it was shown; review it again")
        self.policy = policy


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")


def backup_time(name: str) -> datetime | None:
    """UTC time encoded in a backup name (aquacrop-2026-10-12T023000.123456Z.sql.gz)."""
    if not name.startswith("aquacrop-") or not name.endswith(".sql.gz"):
        return None
    stamp = name[len("aquacrop-"):-len(".sql.gz")]
    for fmt in ("%Y-%m-%dT%H%M%S.%fZ", "%Y-%m-%dT%H%M%SZ"):
        try:
            return datetime.strptime(stamp, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def select_keep(names: list[str], daily: int, monthly: int = 0) -> set[str]:
    """Copies a daily/monthly policy keeps: the newest copy of each of the newest `daily` UTC days, the
    first copy of each of the newest `monthly` months, and always the newest copy. Names whose time
    cannot be read are always kept."""
    dated = sorted((t, n) for n in names if (t := backup_time(n)) is not None)
    keep = {n for n in names if backup_time(n) is None}
    days: dict = {}
    months: dict = {}
    for t, n in dated:
        days[t.date()] = n
        months.setdefault((t.year, t.month), n)
    keep |= {days[d] for d in sorted(days)[-daily:]} if daily else set()
    keep |= {months[m] for m in sorted(months)[-monthly:]} if monthly else set()
    if dated:
        keep.add(dated[-1][1])
    return keep


def policy_view(cfg) -> dict:
    p = cfg.offsite_backup.policy.model_dump()
    return {"local_daily": p["local_daily"], "cloud_daily": p["cloud_daily"], "cloud_monthly": p["cloud_monthly"],
            "policy_hash": hashlib.sha256(json.dumps(p, sort_keys=True).encode()).hexdigest()[:16]}


class OffsiteStore:
    """The state files (shared by the CLI processes and the server, which only reads them)."""

    def __init__(self, state_dir: Path):
        self.dir = Path(state_dir) / "offsite"
        self.queue_dir = self.dir / "queue"
        self.drills_dir = self.dir / "drills"

    def _read(self, name: str, default):
        try:
            return json.loads((self.dir / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def status(self) -> dict:
        return self._read("status.json", {})

    def update_status(self, **parts) -> dict:
        self.dir.mkdir(parents=True, exist_ok=True)
        st = self.status() | parts
        _write_atomic(self.dir / "status.json", json.dumps(st, indent=2, default=str))
        return st

    def ledger(self) -> list[dict]:
        return self._read("ledger.json", [])

    def save_ledger(self, rows: list[dict]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        _write_atomic(self.dir / "ledger.json", json.dumps(sorted(rows, key=lambda r: r["name"]), indent=2))

    def queue(self) -> list[dict]:
        out = []
        for p in sorted(self.queue_dir.glob("*.json")) if self.queue_dir.exists() else []:
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(out, key=lambda e: e["name"])

    def put_queue(self, entry: dict) -> None:
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        _write_atomic(self.queue_dir / (entry["name"] + ".json"), json.dumps(entry, indent=2))

    def drop_queue(self, name: str) -> None:
        (self.queue_dir / (name + ".json")).unlink(missing_ok=True)

    def confirmed_policy(self) -> dict | None:
        return self._read("policy-confirmed.json", None)

    def journal(self, kind: str, payload: dict, actor: str = "system") -> dict:
        """Append one outcome. Lines are single small writes in append mode; `jid` identifies each one
        for the server's de-duplicated copy into the events table."""
        self.dir.mkdir(parents=True, exist_ok=True)
        j = self.dir / "journal.jsonl"
        if j.exists() and j.stat().st_size > JOURNAL_MAX_BYTES:
            os.replace(j, self.dir / "journal.1.jsonl")
        entry = {"jid": f"{time.time_ns():x}-{os.getpid():x}", "utc": _iso(time.time()), "kind": kind,
                 "actor": actor, "payload": payload}
        with open(j, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
        return entry

    def journal_entries(self) -> list[dict]:
        out = []
        for name in ("journal.1.jsonl", "journal.jsonl"):
            p = self.dir / name
            if not p.exists():
                continue
            for line in p.read_text(encoding="utf-8").splitlines():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue       # a line cut by a crash is ignored, never half-applied
        return out


class Offsite:
    """The operations the CLI and the timers run. `probe` and `rclone` are replaceable for tests."""

    def __init__(self, cfg, state_dir: Path, db_path: Path, *, probe: VolumeProbe | None = None,
                 rclone: Rclone | None = None, now=time.time):
        self.cfg, self.s = cfg, cfg.offsite_backup
        self.state_dir, self.db_path = Path(state_dir), Path(db_path)
        self.store = OffsiteStore(self.state_dir)
        self.probe = probe or VolumeProbe()
        c = self.s.cloud
        self.rclone = rclone or (Rclone(c.rclone_binary, c.rclone_config, c.remote, c.transfer_timeout_s)
                                 if c.remote else None)
        self.now = now
        self._lock = None

    # ------------------------------------------------------------------ plumbing
    def __enter__(self) -> "Offsite":
        from ..ownership import OwnershipError, OwnerLock
        try:
            self._lock = OwnerLock(self.store.dir / "offsite.lock").acquire()
        except OwnershipError:
            raise Busy("another backup, upload or drill is running") from None
        return self

    def __exit__(self, *exc) -> None:
        if self._lock:
            self._lock.release()
            self._lock = None

    def _require_enabled(self) -> None:
        if not self.s.enabled:
            raise NotActivated("SD-card and cloud backups are not activated: the owner has not yet confirmed the "
                               "SD card and the OneDrive account/folder (offsite_backup.enabled is false)")

    def card(self) -> Card:
        live = [self.db_path.parent, self.state_dir]
        try:
            c = verify_card(self.s.sd_card, live_paths=live, probe=self.probe)
        except VolumeError as e:
            self.store.update_status(card={"ok": False, "code": e.code, "error": str(e), "utc": _iso(self.now())})
            raise
        self.store.update_status(card={"ok": True, "utc": _iso(self.now())} | c.view())
        return c

    def backup_dir(self, card: Card) -> Path:
        return card.mount_path / self.s.sd_card.backup_subdir

    def _fail(self, op: str, code: str, error: str, **extra) -> dict:
        res = {"ok": False, "code": code, "error": error, "utc": _iso(self.now())} | extra
        self.store.journal(f"OFFSITE_{op}_FAILED", res)
        self.store.update_status(**{f"last_{op.lower()}": res})
        return res

    # ------------------------------------------------------------------ daily backup
    def backup(self, *, actor: str = "system", upload: bool = True, prune: bool = True) -> dict:
        """The daily job: SD backup, then queue it, try the queue and prune under a confirmed policy."""
        self._require_enabled()
        try:
            card = self.card()
        except VolumeError as e:
            return self._fail("BACKUP", e.code, str(e))
        dest = self.backup_dir(card)
        dest.mkdir(parents=True, exist_ok=True)
        if not on_card(card, dest, self.probe):          # e.g. a symlink planted on the card
            return self._fail("BACKUP", "SD_CARD_WRONG", f"{dest} does not resolve to the verified card")
        h = self.cfg.historian
        est = estimate_backup_bytes(self.db_path, dest)["estimate_bytes"]
        free = self.probe.usage(dest).free
        if free - est < self.s.sd_card.reserve_bytes:
            return self._fail("BACKUP", "NO_SPACE", f"the card has {free / 2**30:.2f} GiB free; the next backup is "
                              f"estimated at {est / 2**30:.2f} GiB and {self.s.sd_card.reserve_bytes / 2**30:.2f} GiB "
                              "must stay free. Nothing was written; earlier backups are kept.", free_bytes=free)
        try:
            man = create_backup(self.db_path, dest, config_id=self.cfg.config_id,
                                config_version=self.cfg.config_version, reserve_bytes=self.s.sd_card.reserve_bytes,
                                wal_abort_bytes=h.wal_reader_abort_bytes, max_seconds=h.backup_max_seconds)
        except BackupRefused as e:
            return self._fail("BACKUP", "NO_SPACE", str(e), free_bytes=card.free_bytes)
        except BackupAborted as e:
            return self._fail("BACKUP", "ABORTED", str(e))
        except BackupFailed as e:
            return self._fail("BACKUP", "VERIFY_FAILED", str(e))
        except OSError as e:                              # the card went away or failed while writing
            return self._fail("BACKUP", "WRITE_ERROR", repr(e))
        path = Path(man["path"])
        v = verify_backup(path)                          # re-read from the card: checksum, stream, counts
        if not v["ok"]:
            manifest_path(path).unlink(missing_ok=True)   # unlisted first, then set aside for inspection
            os.replace(path, path.with_name(path.name + ".bad"))
            return self._fail("BACKUP", "VERIFY_FAILED", f"read-back from the card failed: {v.get('integrity')}",
                              file=path.name)
        res = {"ok": True, "file": man["file"], "bytes": man["bytes"], "sha256": man["sha256"],
               "utc": _iso(self.now()), "newest_sample_utc": man.get("newest_sample_utc"),
               "row_counts_total": sum(man["row_counts"].values()), "card_free_bytes": self.probe.usage(dest).free}
        self.store.journal("OFFSITE_BACKUP_CREATED", res, actor=actor)
        self.store.update_status(last_backup=res, local=self._local_view(dest))
        out = {"backup": res}
        if self.rclone is not None:
            self.store.put_queue({"name": man["file"], "manifest": manifest_path(path).name, "sha256": man["sha256"],
                                  "bytes": man["bytes"], "created_utc": res["utc"], "attempts": 0,
                                  "next_attempt_utc": res["utc"], "last_error": None, "last_code": None,
                                  "source_db_used_bytes": man.get("source_db_used_bytes"),
                                  "schema_version": man.get("schema_version")})
            if upload:
                out["upload"] = self.upload()
        if prune:
            out["prune"] = self.prune()
        return out

    def _local_view(self, dest: Path) -> dict:
        gz = [p for p in list_backups(dest) if p.name.endswith(".sql.gz")]
        return {"count": len(gz), "bytes": sum(p.stat().st_size for p in gz), "newest": gz[-1].name if gz else None,
                "oldest": gz[0].name if gz else None}

    # ------------------------------------------------------------------ uploads
    def upload(self, *, force: bool = False) -> dict:
        """Try every due queue entry, oldest first. Stops at the first failure that means 'not now'
        (offline, authorization, card missing); the entries stay queued."""
        self._require_enabled()
        if self.rclone is None:
            return {"skipped": "cloud copy not configured"}
        q = self.store.queue()
        now = self.now()
        due = [e for e in q if force or datetime.fromisoformat(e["next_attempt_utc"]).timestamp() <= now]
        res = {"queued": len(q), "due": len(due), "uploaded": [], "failed": []}
        if not due:
            return res
        try:
            card = self.card()
            self.rclone.check_crypt()
        except (VolumeError, RcloneError) as e:
            for ent in due:
                self._upload_failed(ent, e.code, str(e))
            res["failed"] = [e_["name"] for e_ in due]
            self.store.update_status(last_upload={"ok": False, "code": e.code, "error": str(e),
                                                  "utc": _iso(self.now()), "pending": len(q)})
            return res
        src = self.backup_dir(card)
        for i, ent in enumerate(due):
            local = src / ent["name"]
            man = src / ent["manifest"]
            try:
                if not local.exists() or not man.exists():
                    raise RcloneError(f"{ent['name']} is no longer on the card", code="LOCAL_MISSING")
                if _sha256(local) != ent["sha256"]:
                    raise RcloneError(f"{ent['name']} on the card no longer matches its checksum",
                                      code="LOCAL_CORRUPT")
                self.rclone.upload(local, ent["name"])
                self.rclone.upload(man, ent["manifest"])
                self.rclone.cryptcheck(src, [ent["name"], ent["manifest"]])
            except RcloneError as e:
                self._upload_failed(ent, e.code, str(e))
                res["failed"].append(ent["name"])
                if e.code in STOP_ON:
                    break
                continue
            row = {k: ent[k] for k in ("name", "manifest", "sha256", "bytes", "created_utc", "source_db_used_bytes",
                                       "schema_version")} | {"uploaded_utc": _iso(self.now()), "verified": "cryptcheck"}
            ledger = [r for r in self.store.ledger() if r["name"] != ent["name"]] + [row]
            self.store.save_ledger(ledger)
            self.store.drop_queue(ent["name"])
            self.store.journal("OFFSITE_UPLOADED", {"file": ent["name"], "bytes": ent["bytes"], "attempts":
                                                    ent["attempts"] + 1, "verified": "cryptcheck"})
            res["uploaded"].append(ent["name"])
        q_now = self.store.queue()
        last = {"ok": not res["failed"], "utc": _iso(self.now()), "uploaded": res["uploaded"],
                "failed": res["failed"], "pending": len(q_now)}
        if res["failed"]:
            failed = next(e for e in q_now if e["name"] == res["failed"][-1])
            last |= {"code": failed["last_code"], "error": failed["last_error"]}
        self.store.update_status(last_upload=last, cloud=self._cloud_view())
        return res

    def _upload_failed(self, ent: dict, code: str, error: str) -> None:
        c = self.s.cloud
        n = ent["attempts"] + 1
        wait = min(c.retry_max_s, c.retry_initial_s * 2 ** (n - 1))
        ent |= {"attempts": n, "last_error": error[-500:], "last_code": code,
                "last_attempt_utc": _iso(self.now()), "next_attempt_utc": _iso(self.now() + wait)}
        self.store.put_queue(ent)
        self.store.journal("OFFSITE_UPLOAD_FAILED", {"file": ent["name"], "code": code, "error": error[-500:],
                                                     "attempts": n, "next_attempt_utc": ent["next_attempt_utc"]})

    def _cloud_view(self) -> dict:
        led = self.store.ledger()
        return {"copies": len(led), "bytes": sum(r.get("bytes") or 0 for r in led),
                "newest": led[-1]["name"] if led else None, "oldest": led[0]["name"] if led else None}

    # ------------------------------------------------------------------ pruning
    def plan(self, card: Card | None = None, listing: list[dict] | None = None) -> dict:
        """What the policy would delete now. Read-only."""
        pol, conf = policy_view(self.cfg), self.store.confirmed_policy()
        active = bool(conf and conf.get("policy_hash") == pol["policy_hash"])
        out = {"policy": pol, "confirmed": conf, "active": active, "utc": _iso(self.now())}
        names_local: list[str] = []
        if card is not None:
            d = self.backup_dir(card)
            names_local = [p.name for p in list_backups(d) if p.name.endswith(".sql.gz")] if d.exists() else []
        queued = {e["name"] for e in self.store.queue()}
        keep_l = select_keep(names_local, pol["local_daily"]) | queued
        out["local"] = {"available": card is not None, "count": len(names_local),
                        "delete": [n for n in names_local if n not in keep_l],
                        "kept_for_upload": sorted(queued & set(names_local))}
        led = [r["name"] for r in self.store.ledger()]
        keep_c = select_keep(led, pol["cloud_daily"], pol["cloud_monthly"])
        cloud_del = [n for n in led if n not in keep_c]
        out["cloud"] = {"available": self.rclone is not None, "count": len(led), "delete": cloud_del}
        if listing is not None:
            present = {e["name"] for e in listing}
            out["cloud"]["missing_in_cloud"] = [n for n in led if n not in present]
            out["cloud"]["not_in_ledger"] = sorted(n for n in present if n.endswith(".sql.gz") and n not in set(led))
        newest = max([t for n in names_local + led if (t := backup_time(n))], default=None)
        if newest is not None and newest.timestamp() > self.now() + FUTURE_SLACK_S:
            out["refused"] = (f"a backup is named {newest.isoformat()}, later than the clock "
                              f"({_iso(self.now())}); nothing is pruned until the date is checked")
        return out

    def prune(self, *, actor: str = "system") -> dict:
        self._require_enabled()
        try:
            card = self.card()
        except VolumeError:
            card = None
        listing = None
        if self.rclone is not None:
            try:
                listing = self.rclone.listing()
                self.store.update_status(listing={"utc": _iso(self.now()), "files": len(listing),
                                                  "backups": sum(1 for e in listing if e["name"].endswith(".sql.gz"))})
            except RcloneError as e:
                self.store.update_status(listing={"utc": _iso(self.now()), "error": str(e), "code": e.code})
        p = self.plan(card, listing)
        self.store.update_status(plan=p)
        if not p["active"] or p.get("refused"):
            return p | {"pruned": False}
        deleted_l, deleted_c = [], []
        if card is not None:
            d = self.backup_dir(card)
            for n in p["local"]["delete"]:
                f = d / n
                manifest_path(f).unlink(missing_ok=True)
                f.unlink(missing_ok=True)
                deleted_l.append(n)
        if self.rclone is not None and listing is not None:
            present = {e["name"] for e in listing}
            led = {r["name"]: r for r in self.store.ledger()}
            for n in p["cloud"]["delete"]:
                try:
                    if n in present:
                        self.rclone.delete(n)
                    if led[n]["manifest"] in present:
                        self.rclone.delete(led[n]["manifest"])
                except RcloneError as e:
                    self.store.journal("OFFSITE_PRUNE_FAILED", {"file": n, "code": e.code, "error": str(e)})
                    break
                led.pop(n)
                deleted_c.append(n)
            self.store.save_ledger(list(led.values()))
        if deleted_l or deleted_c:
            self.store.journal("OFFSITE_PRUNED", {"local": deleted_l, "cloud": deleted_c,
                                                  "policy": p["policy"], "confirmed_by": p["confirmed"]["confirmed_by"]},
                               actor=actor)
        st = {"cloud": self._cloud_view()}
        if card is not None:
            st["local"] = self._local_view(self.backup_dir(card))
        self.store.update_status(**st)
        return p | {"pruned": True, "deleted_local": deleted_l, "deleted_cloud": deleted_c}

    # ------------------------------------------------------------------ restore drill
    def drill(self, *, actor: str = "system") -> dict:
        """Download the newest verified cloud copy into SD-card scratch space and run the full restore
        check there. Never touches the production database."""
        self._require_enabled()
        if self.rclone is None:
            return self._fail("DRILL", "NOT_CONFIGURED", "the cloud copy is not configured")
        try:
            card = self.card()
        except VolumeError as e:
            return self._fail("DRILL", e.code, str(e))
        led = self.store.ledger()
        if not led:
            return self._fail("DRILL", "NO_CLOUD_COPY", "no verified cloud copy yet")
        src = led[-1]
        d = self.s.drill
        used = src.get("source_db_used_bytes") or 0
        need = max(d.min_scratch_bytes, (src.get("bytes") or 0) + int(used * 1.2)) + d.headroom_bytes
        root = card.mount_path / self.s.sd_card.drill_subdir
        root.mkdir(parents=True, exist_ok=True)
        if not on_card(card, root, self.probe):
            return self._fail("DRILL", "SD_CARD_WRONG", f"{root} does not resolve to the verified card")
        live = self.db_path.resolve().parent
        if root.resolve() == live or live in root.resolve().parents:
            return self._fail("DRILL", "UNSAFE_SCRATCH", "the drill scratch space is inside the live state directory")
        for stale in root.glob("drill-*"):        # left by a drill that crashed or lost power (we hold the lock)
            shutil.rmtree(stale, ignore_errors=True)
        free = self.probe.usage(root).free
        if free < need:
            return self._fail("DRILL", "NO_SCRATCH_SPACE",
                              f"preflight: the drill needs {need / 2**30:.2f} GiB free on the card (at least "
                              f"{d.min_scratch_bytes / 2**30:.0f} GiB plus {d.headroom_bytes / 2**30:.2f} GiB headroom); "
                              f"{free / 2**30:.2f} GiB free. Nothing was downloaded.", needed_bytes=need,
                              free_bytes=free, backup=src["name"])
        run = root / f"drill-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{os.getpid()}"
        run.mkdir()
        t0 = time.monotonic()
        report = {"backup": src["name"], "uploaded_utc": src.get("uploaded_utc"), "scratch": str(run),
                  "needed_bytes": need, "free_bytes_before": free}
        try:
            local = run / src["name"]
            self.rclone.download(src["name"], local)
            self.rclone.download(src["manifest"], run / src["manifest"])
            got = _sha256(local)
            man = json.loads((run / src["manifest"]).read_text(encoding="utf-8"))
            report["download_seconds"] = round(time.monotonic() - t0, 1)
            report["checksum_ok"] = got == src["sha256"] == man.get("sha256")
            if not report["checksum_ok"]:
                raise BackupFailed("the downloaded copy does not match the checksum recorded at upload")
            v = verify_backup(local, full=True, scratch_dir=run, scratch_reserve_bytes=d.headroom_bytes,
                              cfg=self.cfg, neutralize_permissions=True)
            report |= {"ok": v["ok"], "integrity": v.get("integrity"), "full": v.get("full")}
        except (RcloneError, BackupFailed, OSError, ValueError) as e:
            report |= {"ok": False, "integrity": str(e), "code": getattr(e, "code", "DRILL_FAILED")}
        finally:
            shutil.rmtree(run, ignore_errors=True)
        report |= {"utc": _iso(self.now()), "seconds": round(time.monotonic() - t0, 1),
                   "scratch_removed": not run.exists(), "production_database": "read only (never written)"}
        self.store.drills_dir.mkdir(parents=True, exist_ok=True)
        _write_atomic(self.store.drills_dir / f"drill-{report['utc'].replace(':', '')}.json",
                      json.dumps(report, indent=2, default=str))
        summary = {k: report.get(k) for k in ("ok", "backup", "utc", "integrity", "seconds", "checksum_ok", "code")}
        self.store.journal("OFFSITE_DRILL_PASSED" if report["ok"] else "OFFSITE_DRILL_FAILED", summary, actor=actor)
        self.store.update_status(last_drill=summary)
        return report

    # ------------------------------------------------------------------ policy
    def confirm_policy(self, actor: str, policy_hash: str) -> dict:
        pol = policy_view(self.cfg)
        if policy_hash != pol["policy_hash"]:
            raise PolicyMismatch(pol)
        rec = pol | {"confirmed_by": actor, "confirmed_utc": _iso(self.now())}
        self.store.dir.mkdir(parents=True, exist_ok=True)
        _write_atomic(self.store.dir / "policy-confirmed.json", json.dumps(rec, indent=2))
        return self.store.journal("OFFSITE_POLICY_CONFIRMED", rec, actor=actor)


# ---------------------------------------------------------------------- server side (read-only)
ALARM = "OFFSITE_BACKUP"


class OffsiteMonitor:
    """In the server: shows the state files, copies the journal into the events table (once per
    entry) and raises OFFSITE_BACKUP alarms. Never runs rclone or writes to the card."""

    def __init__(self, cfg, state_dir: Path, hist, *, wall=time.time, monotonic=time.monotonic):
        self.cfg, self.s, self.hist = cfg, cfg.offsite_backup, hist
        self.store = OffsiteStore(state_dir)
        self._wall, self._mono = wall, monotonic
        self.started = wall()
        self._seen: set[str] | None = None
        self._recorded: set[str] = set()      # written by this process directly (may not be committed yet)
        self._next = 0.0

    def _ingest(self) -> None:
        entries = self.store.journal_entries()
        if self._seen is None:
            rows = self.hist.query("SELECT payload_json FROM events WHERE kind >= 'OFFSITE_' AND kind < 'OFFSITE`'")
            self._seen = {json.loads(r[0]).get("jid") for r in rows}
        for e in entries:
            if e.get("jid") in self._seen or e.get("jid") in self._recorded:
                continue
            self._seen.add(e["jid"])
            self.hist.event(e["kind"], dict(e.get("payload") or {}) | {"jid": e["jid"], "journal_utc": e["utc"]},
                            actor=e.get("actor") or "system")

    def record(self, entry: dict) -> None:
        """An entry this process journaled itself (policy confirmation) goes to the events table now."""
        self._recorded.add(entry["jid"])
        self.hist.event(entry["kind"], dict(entry["payload"]) | {"jid": entry["jid"], "journal_utc": entry["utc"]},
                        actor=entry["actor"])

    def tick(self, alarms, sim_s: float | None = None) -> None:
        now = self._mono()
        if now < self._next:
            return
        self._next = now + 30.0
        try:
            self._ingest()
        except Exception:     # a damaged journal must never stop the driver loop; shown in view()
            pass
        if not self.s.enabled:
            for t in ("sd_card", "cloud_upload", "restore_drill"):
                alarms.clear(ALARM, t)
            return
        for target, msg in self.problems().items():
            if msg:
                alarms.raise_or_update(ALARM, target, severity="WARNING", sim_s=sim_s, message=msg)
            else:
                alarms.clear(ALARM, target)

    def problems(self) -> dict:
        st, wall = self.store.status(), self._wall()
        stale = self.s.stale_after_hours * 3600
        out = {"sd_card": None, "cloud_upload": None, "restore_drill": None}
        lb = st.get("last_backup") or {}
        ok_t = datetime.fromisoformat(lb["utc"]).timestamp() if lb.get("ok") else None
        newest_ok = (st.get("local") or {}).get("newest")
        if lb and not lb.get("ok"):
            out["sd_card"] = f"the last SD-card backup failed ({lb.get('code')}): {lb.get('error')}. Earlier backups " \
                             "are kept; recording continues on the eMMC."
        elif wall - (ok_t or self.started) > stale:
            out["sd_card"] = f"no verified SD-card backup for more than {self.s.stale_after_hours:g} h" + \
                             (f" (newest {newest_ok})" if newest_ok else "")
        if self.s.cloud.remote:
            q = self.store.queue()
            oldest = min((datetime.fromisoformat(e["created_utc"]).timestamp() for e in q), default=None)
            bad = [e for e in q if e.get("last_code") in ("AUTHORIZATION", "NOT_ENCRYPTED", "NOT_CONFIGURED",
                                                           "LOCAL_MISSING", "LOCAL_CORRUPT", "CLOUD_FULL",
                                                           "RCLONE_MISSING")]
            if bad:
                out["cloud_upload"] = f"cloud upload needs attention ({bad[0]['last_code']}): {bad[0]['last_error']}"
            elif oldest is not None and wall - oldest > stale:
                out["cloud_upload"] = f"{len(q)} backup(s) waiting for upload, the oldest for " \
                                      f"{(wall - oldest) / 3600:.0f} h (last error: {q[0].get('last_error')}). They " \
                                      "upload automatically when the connection returns."
            ld = st.get("last_drill") or {}
            due_s = self.s.drill.interval_days * 86400 + 5 * 86400
            last_t = datetime.fromisoformat(ld["utc"]).timestamp() if ld.get("utc") else None
            if ld and not ld.get("ok"):
                out["restore_drill"] = f"the last restore drill from the cloud copy failed: {ld.get('integrity') or ld.get('error')}"
            elif wall - (last_t if ld.get("ok") else self.started) > due_s:
                out["restore_drill"] = f"no successful restore drill from the cloud copy in {self.s.drill.interval_days} days"
        return out

    def view(self) -> dict:
        st = self.store.status()
        s = self.s
        pol, conf = policy_view(self.cfg), self.store.confirmed_policy()
        drill = st.get("last_drill")
        due = True
        if drill and drill.get("ok") and drill.get("utc"):
            due = (self._wall() - datetime.fromisoformat(drill["utc"]).timestamp()) > s.drill.interval_days * 86400
        return {
            "enabled": s.enabled,
            "activation": {
                "sd_card_confirmed": bool(s.sd_card.mount_path and s.sd_card.filesystem_uuid and s.sd_card.volume_id),
                "cloud_configured": bool(s.cloud.remote),
                "note": None if s.enabled else "Not activated: the owner must confirm the actual SD card and "
                                               "OneDrive account/folder. Until then the local eMMC backups continue.",
            },
            "sd_card": {"mount_path": s.sd_card.mount_path, "filesystem_uuid": s.sd_card.filesystem_uuid,
                        "last_check": st.get("card")},
            "cloud": {"remote": s.cloud.remote, "account_note": s.cloud.account_note,
                      "listing": st.get("listing")} | (st.get("cloud") or {"copies": 0}),
            "last_backup": st.get("last_backup"), "local": st.get("local"),
            "queue": [{k: e.get(k) for k in ("name", "bytes", "created_utc", "attempts", "last_code", "last_error",
                                             "next_attempt_utc")} for e in self.store.queue()],
            "last_upload": st.get("last_upload"),
            "drill": {"last": drill, "due": due, "interval_days": s.drill.interval_days,
                      "min_scratch_bytes": s.drill.min_scratch_bytes, "headroom_bytes": s.drill.headroom_bytes},
            "policy": pol | {"confirmed": conf, "active": bool(conf and conf.get("policy_hash") == pol["policy_hash"]),
                             "last_plan": st.get("plan")},
            "problems": self.problems() if s.enabled else {},
        }

