"""AT-OFF-01..10: SD-card daily backups and encrypted cloud copies (owner request of 2026-10-12, ADR-0012).

These tests use a fake volume probe (no physical SD card or USB reader) and a fake rclone remote (no
OneDrive account); `test_real_rclone_crypt_round_trip` runs the real rclone binary against a local
crypt remote when rclone is installed (CI). None of this is a test of the physical card on the CM4 or
of the owner's OneDrive account."""
import hashlib
import json
import os
import shutil
import sys
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import aquacrop.historian.backup as backup_mod
import aquacrop.historian.offsite as offsite_mod
from aquacrop.config.schema import OffsiteBackupSettings
from aquacrop.historian.offsite import Offsite, OffsiteMonitor, PolicyMismatch, select_keep
from aquacrop.historian.rclone import Rclone, RcloneError
from aquacrop.historian.sdcard import MARKER, VolumeError, enroll_card, verify_card
from aquacrop.historian.writer import Historian

GIB = 1024**3
UUID = "1A2B-3C4D"
Usage = namedtuple("Usage", "total used free")


class FakeProbe:
    """Kernel view of mounts: `mounts` maps a mount point to a device number; every other path is on
    device 1 (the eMMC)."""

    def __init__(self):
        self.mounts: dict[str, int] = {}
        self.uuids: dict[str, int] = {}
        self.options: dict[str, set] = {}
        self.free: dict[str, int] = {}

    def _mount_of(self, p: Path) -> str | None:
        p = Path(p).resolve()
        for cand in [p, *p.parents]:
            if str(cand) in self.mounts:
                return str(cand)
        return None

    def is_mount(self, p):
        return str(Path(p).resolve()) in self.mounts

    def device_of(self, p):
        m = self._mount_of(p)
        return self.mounts[m] if m else 1

    def device_for_uuid(self, uuid):
        return self.uuids.get(uuid)

    def mount_entry(self, p):
        return {"options": self.options.get(str(Path(p).resolve()), {"rw", "noatime"}), "fstype": "ext4"}

    def usage(self, p):
        m = self._mount_of(p)
        real = shutil.disk_usage(p)
        if m in self.free:
            return Usage(64 * GIB, 64 * GIB - self.free[m], self.free[m])
        return real


class FakeRemote:
    """An rclone crypt remote stand-in: files kept (decrypted view) in a directory."""

    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.offline = False
        self.crypt = True
        self.corrupt_upload = False
        self.calls: list[tuple] = []

    def _gate(self, op):
        self.calls.append((op,))
        if self.offline:
            raise RcloneError(f"rclone {op} exited 1: dial tcp: lookup graph.microsoft.com: no such host",
                              code="OFFLINE")

    def check_crypt(self):
        self._gate("listremotes")
        if not self.crypt:
            raise RcloneError("remote is type 'onedrive', not 'crypt'", code="NOT_ENCRYPTED")

    def upload(self, local, name):
        self._gate("copyto")
        data = Path(local).read_bytes()
        if self.corrupt_upload and name.endswith(".sql.gz"):
            data = data[:-10] + b"x" * 10
        (self.root / name).write_bytes(data)

    def cryptcheck(self, local_dir, names):
        self._gate("cryptcheck")
        for n in names:
            r = self.root / n
            if not r.exists() or r.read_bytes() != (Path(local_dir) / n).read_bytes():
                raise RcloneError(f"cryptcheck: {n} differs", code="RCLONE_FAILED")

    def listing(self):
        self._gate("lsjson")
        return [{"name": p.name, "bytes": p.stat().st_size} for p in self.root.iterdir()]

    def download(self, name, local):
        self._gate("copyto")
        shutil.copyfile(self.root / name, local)

    def delete(self, name):
        self._gate("deletefile")
        (self.root / name).unlink()


class Clock:
    def __init__(self):
        self.t = datetime.now(timezone.utc).timestamp()

    def __call__(self):
        return self.t


def _cfg(cfg24, tmp_path, **sd):
    off = OffsiteBackupSettings.model_validate({
        "enabled": True,
        "sd_card": {"mount_path": str(tmp_path / "sd"), "filesystem_uuid": UUID, "volume_id": sd.get("vol"),
                    "reserve_bytes": 0},
        "cloud": {"remote": "aquacrop-crypt:greenhouse", "rclone_config": str(tmp_path / "rclone.conf"),
                  "retry_initial_s": 300, "retry_max_s": 3600},
        "drill": {"min_scratch_bytes": 10 * GIB, "headroom_bytes": 1 * 1024**2}})
    return cfg24.model_copy(update={"offsite_backup": off})


@pytest.fixture
def env(tmp_path, cfg24):
    """Live state on the 'eMMC' (device 1), an enrolled card mounted at tmp/sd (device 7), a fake cloud."""
    state = tmp_path / "state"
    state.mkdir()
    sd = tmp_path / "sd"
    sd.mkdir()
    probe = FakeProbe()
    probe.mounts[str(sd.resolve())] = 7
    probe.uuids[UUID] = 7
    probe.free[str(sd.resolve())] = 32 * GIB
    vol = enroll_card(str(sd), UUID, live_paths=[state], probe=probe)["config"]["volume_id"]
    cfg = _cfg(cfg24, tmp_path, vol=vol)
    db = state / "demo-24.sqlite3"
    h = Historian(db, cfg, "s1")
    for i in range(20):
        h.event("TEST_EVENT", {"i": i}, actor="t")
    h.wait_idle()
    remote = FakeRemote(tmp_path / "cloud")
    clock = Clock()
    off = Offsite(cfg, state, db, probe=probe, rclone=remote, now=clock)

    class E:
        pass
    e = E()
    e.__dict__.update(state=state, sd=sd, probe=probe, cfg=cfg, db=db, hist=h, remote=remote, clock=clock, off=off,
                      bdir=sd / "aquacrop" / "backups")
    yield e
    h.close()


def _journal_kinds(e):
    return [x["kind"] for x in e.off.store.journal_entries()]


def test_card_identity_checks_fail_closed(env, tmp_path):
    """AT-OFF-01: missing card, another card, the eMMC itself, read-only, no marker: all refused."""
    sd, probe, s = env.sd, env.probe, env.cfg.offsite_backup.sd_card
    live = [env.state]
    assert verify_card(s, live_paths=live, probe=probe).volume_id == s.volume_id
    cases = []
    p = FakeProbe()                                            # card missing: the mount point is a plain dir
    p.uuids[UUID] = 7
    cases.append((p, "SD_CARD_MISSING"))
    p = FakeProbe()                                            # another card mounted there
    p.mounts[str(sd.resolve())] = 8
    p.uuids[UUID] = 7
    cases.append((p, "SD_CARD_WRONG"))
    p = FakeProbe()                                            # the confirmed UUID is not attached at all
    p.mounts[str(sd.resolve())] = 8
    cases.append((p, "SD_CARD_WRONG"))
    p = FakeProbe()                                            # read-only
    p.mounts[str(sd.resolve())] = 7
    p.uuids[UUID] = 7
    p.options[str(sd.resolve())] = {"ro"}
    cases.append((p, "SD_CARD_READ_ONLY"))
    for probe_, code in cases:
        with pytest.raises(VolumeError) as ei:
            verify_card(s, live_paths=live, probe=probe_)
        assert ei.value.code == code
    # the live database's filesystem is never accepted as the card
    p = FakeProbe()
    p.mounts[str(sd.resolve())] = 1
    p.uuids[UUID] = 1
    with pytest.raises(VolumeError) as ei:
        verify_card(s, live_paths=live, probe=p)
    assert ei.value.code == "SD_CARD_WRONG"
    # marker missing or from another enrolment
    mk = json.loads((sd / MARKER).read_text())
    (sd / MARKER).write_text(json.dumps(mk | {"volume_id": "vol_" + "0" * 16}))
    with pytest.raises(VolumeError) as ei:
        verify_card(s, live_paths=live, probe=probe)
    assert ei.value.code == "SD_CARD_WRONG"
    (sd / MARKER).unlink()
    with pytest.raises(VolumeError):
        verify_card(s, live_paths=live, probe=probe)
    # enrolment is idempotent and refuses a marker from another filesystem
    (sd / MARKER).write_text(json.dumps(mk))
    assert enroll_card(str(sd), UUID, live_paths=live, probe=probe)["created"] is False
    (sd / MARKER).write_text(json.dumps(mk | {"filesystem_uuid": "FFFF-0000"}))
    with pytest.raises(VolumeError):
        enroll_card(str(sd), UUID, live_paths=live, probe=probe)


def test_missing_card_writes_nothing_and_never_falls_back(env):
    """AT-OFF-02: with the card gone the mount point is an empty eMMC directory: nothing is written
    there or anywhere else, the failure is recorded, earlier backups on the card are untouched."""
    first = env.off.backup()
    assert first["backup"]["ok"]
    on_card = sorted(p.name for p in env.bdir.iterdir())
    del env.probe.mounts[str(env.sd.resolve())]               # card pulled; directory still exists
    state_before = sorted(str(p) for p in env.state.rglob("*") if "offsite" not in p.parts)
    res = env.off.backup()
    assert res == {**res, "ok": False, "code": "SD_CARD_MISSING"}
    assert sorted(p.name for p in env.bdir.iterdir()) == on_card          # (the dir is still the test dir)
    assert sorted(str(p) for p in env.state.rglob("*") if "offsite" not in p.parts) == state_before
    assert not list(env.state.rglob("*.sql.gz*"))
    assert _journal_kinds(env)[-1] == "OFFSITE_BACKUP_FAILED"


def test_backup_verified_queued_offline_then_uploaded_when_back(env):
    """AT-OFF-03/04/05: daily backup with checksum and manifest; upload fails offline and is queued with
    backoff while recording continues; after connectivity returns the whole queue is uploaded and
    verified with cryptcheck, and a completion receipt goes up last; the queue survives a restart."""
    env.remote.offline = True
    r1 = env.off.backup()
    b1 = r1["backup"]["file"]
    assert (env.bdir / b1).exists() and backup_mod.manifest_path(env.bdir / b1).exists()
    man = json.loads(backup_mod.manifest_path(env.bdir / b1).read_text())
    assert man["sha256"] == hashlib.sha256((env.bdir / b1).read_bytes()).hexdigest()
    assert r1["upload"]["failed"] == [b1]
    q = env.off.store.queue()
    assert len(q) == 1 and q[0]["attempts"] == 1 and q[0]["last_code"] == "OFFLINE"
    # recording continues while offline
    n0 = env.hist.query("SELECT count(*) FROM events")[0][0]
    env.hist.event("TEST_EVENT", {"offline": True}, actor="t")
    env.hist.wait_idle()
    assert env.hist.query("SELECT count(*) FROM events")[0][0] == n0 + 1
    # not due yet: no attempt
    assert env.off.upload()["due"] == 0
    env.clock.t += 86400                                        # next day, still offline
    r2 = env.off.backup()
    b2 = r2["backup"]["file"]
    assert {e["name"] for e in env.off.store.queue()} == {b1, b2}
    # restart: a new process reads the same queue
    env.remote.offline = False
    env.clock.t += 3600
    fresh = Offsite(env.cfg, env.state, env.db, probe=env.probe, rclone=env.remote, now=env.clock)
    res = fresh.upload()
    assert sorted(res["uploaded"]) == sorted([b1, b2]) and fresh.store.queue() == []
    assert {r["name"] for r in fresh.store.ledger()} == {b1, b2}
    assert all(r["verified"] == "cryptcheck" for r in fresh.store.ledger())
    assert (env.remote.root / b1).read_bytes() == (env.bdir / b1).read_bytes()
    assert not any(c[0] in ("sync", "move", "delete", "purge") for c in env.remote.calls)
    for b in (b1, b2):                                          # the receipt is the last file of a copy
        rec = json.loads((env.remote.root / offsite_mod.receipt_name(b)).read_text())
        assert rec["file"] == b and rec["sha256"] == hashlib.sha256((env.bdir / b).read_bytes()).hexdigest()
        assert rec["verified"] == "cryptcheck" and rec["manifest"] == backup_mod.manifest_path(env.bdir / b).name
    assert [r["receipt"] for r in fresh.store.ledger()] == [offsite_mod.receipt_name(b) for b in (b1, b2)]
    assert not list((env.state / "offsite" / "receipts").iterdir())
    # an upload that does not verify is not recorded as a cloud copy and stays queued
    env.remote.corrupt_upload = True
    env.clock.t += 86400
    r3 = env.off.backup()
    assert r3["upload"]["failed"] == [r3["backup"]["file"]]
    assert r3["backup"]["file"] not in {r["name"] for r in env.off.store.ledger()}
    assert not (env.remote.root / offsite_mod.receipt_name(r3["backup"]["file"])).exists()   # incomplete
    assert env.off.plan(None, env.remote.listing())["cloud"]["without_receipt"] == [r3["backup"]["file"]]
    # a non-crypt remote is refused before anything is uploaded
    env.remote.corrupt_upload, env.remote.crypt = False, False
    env.clock.t += 86400
    r4 = env.off.backup()
    assert r4["upload"]["failed"] and r4["backup"]["file"] not in os.listdir(env.remote.root)


def test_failed_creation_or_verification_keeps_earlier_backups(env, monkeypatch):
    """AT-OFF-06: a backup that fails verification (before or after publication) or does not fit leaves
    the earlier good backups in place and queues nothing."""
    env.remote.offline = True
    good = env.off.backup()["backup"]["file"]
    before = sorted(p.name for p in env.bdir.iterdir())
    real = backup_mod._verify_sql_gz
    monkeypatch.setattr(backup_mod, "_verify_sql_gz", lambda *a, **k: real(*a, **k) | {"ok": False,
                                                                                         "integrity": "injected"})
    env.clock.t += 86400
    r = env.off.backup()
    assert r["code"] == "VERIFY_FAILED"
    assert sorted(p.name for p in env.bdir.iterdir()) == before and len(env.off.store.queue()) == 1
    monkeypatch.setattr(backup_mod, "_verify_sql_gz", real)
    # read-back from the card fails after publication: the new file is unlisted and set aside
    monkeypatch.setattr(offsite_mod, "verify_backup", lambda p, **k: {"ok": False, "integrity": "bad sector"})
    r = env.off.backup()
    assert r["code"] == "VERIFY_FAILED"
    listed = [p.name for p in backup_mod.list_backups(env.bdir)]
    assert good in listed and len([n for n in listed if n.endswith(".sql.gz")]) == 1
    assert len(list(env.bdir.glob("*.bad"))) == 1
    # the card disappears during the read-back: recorded as a failure, not an exception out of the timer
    def gone(p, **k):
        raise FileNotFoundError(p)
    monkeypatch.setattr(offsite_mod, "verify_backup", gone)
    r = env.off.backup()
    assert r["code"] == "VERIFY_FAILED" and "FileNotFoundError" in r["error"]
    assert env.off.store.status()["last_backup"]["code"] == "VERIFY_FAILED"
    assert good in [p.name for p in backup_mod.list_backups(env.bdir)] and len(env.off.store.queue()) == 1
    monkeypatch.undo()
    # card full
    env.probe.free[str(env.sd.resolve())] = 1024
    r = env.off.backup()
    assert r["code"] == "NO_SPACE" and good in [p.name for p in backup_mod.list_backups(env.bdir)]
    assert [e["name"] for e in env.off.store.queue()] == [good]


def _names(start: datetime, days: int) -> list[str]:
    return [f"aquacrop-{(start + timedelta(days=i)).strftime('%Y-%m-%dT%H%M%S.%fZ')}.sql.gz" for i in range(days)]


def test_copy_policy_selection():
    names = _names(datetime(2026, 1, 1, 2, 30, tzinfo=timezone.utc), 400)
    keep = select_keep(names, 30, 12)
    assert set(names[-30:]) <= keep
    firsts = [n for n in names if n[15:17] == "01"]               # the first of each month
    assert set(firsts[-12:]) <= keep and len(keep) <= 30 + 12
    assert select_keep(names, 7) == set(names[-7:])
    assert select_keep(["odd-name.sql.gz"] + names[:3], 1) == {"odd-name.sql.gz", names[2]}


def test_pruning_waits_for_confirmation_and_never_deletes_unuploaded(env):
    """AT-OFF-07: the proposed 7 local / 30+12 cloud policy is shown first; nothing is deleted until an
    administrator confirms exactly that policy; a changed policy pauses pruning again; queued
    (not yet uploaded) backups and copies not in the ledger are never pruned."""
    env.bdir.mkdir(parents=True, exist_ok=True)
    start = datetime.fromtimestamp(env.clock.t, timezone.utc) - timedelta(days=50)
    names = _names(start, 45)
    for n in names:
        (env.bdir / n).write_bytes(b"x")
        backup_mod.manifest_path(env.bdir / n).write_text("{}")
        (env.remote.root / n).write_bytes(b"x")
        (env.remote.root / backup_mod.manifest_path(Path(n)).name).write_text("{}")
        if n != names[-1]:
            (env.remote.root / offsite_mod.receipt_name(n)).write_text("{}")
    env.off.store.save_ledger([{"name": n, "manifest": backup_mod.manifest_path(Path(n)).name, "sha256": "0",
                                "bytes": 1, "created_utc": "x", "receipt": offsite_mod.receipt_name(n)}
                               for n in names[:-1]])   # newest not uploaded yet
    env.off.store.put_queue({"name": names[-1], "manifest": "m", "sha256": "0", "bytes": 1, "created_utc": "x",
                             "attempts": 0, "next_attempt_utc": "2999-01-01T00:00:00+00:00"})
    (env.remote.root / "aquacrop-2026-01-01T000000.000000Z.sql.gz").write_bytes(b"not ours")
    p = env.off.prune()
    assert p["pruned"] is False and not p["active"] and len(p["local"]["delete"]) == 45 - 7
    assert len(list(env.bdir.glob("*.sql.gz"))) == 45                       # nothing deleted yet
    pol = p["policy"]
    assert (pol["local_daily"], pol["cloud_daily"], pol["cloud_monthly"]) == (7, 30, 12)
    with pytest.raises(PolicyMismatch):
        env.off.confirm_policy("admin1", "0" * 16)
    env.off.confirm_policy("admin1", pol["policy_hash"])
    p = env.off.prune()
    assert p["pruned"] and len(list(env.bdir.glob("*.sql.gz"))) == 7 and names[-1] in os.listdir(env.bdir)
    kept_cloud = {r["name"] for r in env.off.store.ledger()}
    assert set(names[-31:-1]) <= kept_cloud and len(kept_cloud) <= 30 + 12
    assert "aquacrop-2026-01-01T000000.000000Z.sql.gz" in os.listdir(env.remote.root)   # never in the ledger
    assert set(p["cloud"]["not_in_ledger"]) == {"aquacrop-2026-01-01T000000.000000Z.sql.gz", names[-1]}
    in_cloud = set(os.listdir(env.remote.root))
    for n in p["deleted_cloud"]:                                   # the copy, its manifest and its receipt
        assert not {n, backup_mod.manifest_path(Path(n)).name, offsite_mod.receipt_name(n)} & in_cloud
    assert all(offsite_mod.receipt_name(n) in in_cloud for n in kept_cloud)
    assert "OFFSITE_PRUNED" in _journal_kinds(env)
    # a changed policy needs a new confirmation
    cfg2 = env.cfg.model_copy(update={"offsite_backup": env.cfg.offsite_backup.model_copy(
        update={"policy": env.cfg.offsite_backup.policy.model_copy(update={"local_daily": 3})})})
    off2 = Offsite(cfg2, env.state, env.db, probe=env.probe, rclone=env.remote, now=env.clock)
    p2 = off2.prune()
    assert not p2["active"] and p2["pruned"] is False and len(list(env.bdir.glob("*.sql.gz"))) == 7
    # backup names later than the clock: nothing is pruned
    (env.bdir / _names(start + timedelta(days=400), 1)[0]).write_bytes(b"x")
    backup_mod.manifest_path(env.bdir / _names(start + timedelta(days=400), 1)[0]).write_text("{}")
    p3 = env.off.prune()
    assert p3.get("refused") and p3["pruned"] is False


def test_monthly_drill_from_cloud_copy_into_card_scratch(env):
    """AT-OFF-08: download + decrypt (fake) the newest verified cloud copy into SD scratch space, full
    restore check there, sessions and bench leases removed from the copy, scratch removed, production
    database untouched. Preflight refuses without 10 GiB plus headroom; a corrupted download fails."""
    env.off.backup()
    prod = hashlib.sha256(env.db.read_bytes()).hexdigest()
    crashed = env.sd / "aquacrop" / "drill-scratch" / "drill-20260101T000000Z-1"   # power lost mid-drill
    crashed.mkdir(parents=True)
    (crashed / "verify.sqlite3").write_bytes(b"x" * 1000)
    rep = env.off.drill()
    assert rep["ok"], rep
    assert rep["checksum_ok"] and rep["scratch_removed"]
    assert "permissions_removed" in rep["full"] and str(env.sd.resolve()) in rep["scratch"]
    assert hashlib.sha256(env.db.read_bytes()).hexdigest() == prod
    assert not list((env.sd / "aquacrop" / "drill-scratch").iterdir())
    assert _journal_kinds(env)[-1] == "OFFSITE_DRILL_PASSED"
    # preflight: less than 10 GiB + headroom free on the card -> refused before any download
    env.probe.free[str(env.sd.resolve())] = 10 * GIB
    n_calls = len(env.remote.calls)
    rep = env.off.drill()
    assert rep["code"] == "NO_SCRATCH_SPACE" and len(env.remote.calls) == n_calls
    env.probe.free[str(env.sd.resolve())] = 32 * GIB
    # a damaged cloud copy fails the drill
    name = env.off.store.ledger()[-1]["name"]
    data = (env.remote.root / name).read_bytes()
    (env.remote.root / name).write_bytes(data[:-5] + b"xxxxx")
    rep = env.off.drill()
    assert rep["ok"] is False and rep["checksum_ok"] is False
    assert _journal_kinds(env)[-1] == "OFFSITE_DRILL_FAILED"
    # the drill never runs on scratch space outside the verified card
    del env.probe.mounts[str(env.sd.resolve())]
    assert env.off.drill()["code"] == "SD_CARD_MISSING"


def test_not_activated_until_owner_confirms(cfg24, tmp_path):
    """AT-OFF-09: the shipped configuration is disabled and TBD; nothing runs until activation."""
    assert cfg24.offsite_backup.enabled is False and cfg24.offsite_backup.sd_card.filesystem_uuid is None
    assert cfg24.offsite_backup.cloud.remote is None
    off = Offsite(cfg24, tmp_path, tmp_path / "x.sqlite3", probe=FakeProbe())
    with pytest.raises(offsite_mod.NotActivated):
        off.backup()
    with pytest.raises(ValueError):
        OffsiteBackupSettings(enabled=True)
    with pytest.raises(ValueError, match="cloud.remote"):          # no card-only activation
        OffsiteBackupSettings.model_validate({"enabled": True, "sd_card": {
            "mount_path": "/media/aquacrop-sd", "filesystem_uuid": UUID, "volume_id": "vol_0123456789abcdef"}})


def test_monitor_copies_journal_once_and_raises_alarms(env):
    """AT-OFF-10: the server records every outcome once (also after a restart) and alarms on a failed
    backup and on an upload backlog older than the stale limit."""
    from aquacrop.control.alarms import AlarmEngine
    env.remote.offline = True
    env.off.backup()
    del env.probe.mounts[str(env.sd.resolve())]
    env.off.backup()
    alarms = AlarmEngine(env.cfg)
    mon = OffsiteMonitor(env.cfg, env.state, env.hist, wall=env.clock)
    mon.tick(alarms)
    env.hist.wait_idle()
    kinds = [r[0] for r in env.hist.query("SELECT kind FROM events WHERE kind LIKE 'OFFSITE%' ORDER BY id")]
    assert kinds == ["OFFSITE_BACKUP_CREATED", "OFFSITE_UPLOAD_FAILED", "OFFSITE_BACKUP_FAILED"]
    assert alarms.is_active("OFFSITE_BACKUP", "sd_card")
    assert not alarms.is_active("OFFSITE_BACKUP", "cloud_upload")         # backlog still young
    env.clock.t += 40 * 3600
    mon2 = OffsiteMonitor(env.cfg, env.state, env.hist, wall=env.clock)  # restart: no duplicates
    mon2.tick(alarms)
    env.hist.wait_idle()
    assert env.hist.query("SELECT count(*) FROM events WHERE kind LIKE 'OFFSITE%'")[0][0] == 3
    assert alarms.is_active("OFFSITE_BACKUP", "cloud_upload")
    v = mon2.view()
    assert v["enabled"] and v["queue"][0]["last_code"] in ("OFFLINE", "SD_CARD_MISSING")
    assert v["policy"]["active"] is False


@pytest.mark.skipif(os.name == "nt", reason="stub executable uses a POSIX shebang")
def test_rclone_wrapper_issues_only_copy_operations(tmp_path):
    stub = tmp_path / "rclone"
    log = tmp_path / "argv.jsonl"
    stub.write_text(f"#!{sys.executable}\nimport json, sys\nopen({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + "
                    "'\\n')\nif sys.argv[1] == 'listremotes':\n    print('aquacrop-crypt: crypt')\n"
                    "    print('aquacrop-onedrive: onedrive')\n")
    stub.chmod(0o755)
    rc = Rclone(str(stub), str(tmp_path / "rclone.conf"), "aquacrop-crypt:greenhouse")
    rc.check_crypt()
    rc.upload(tmp_path / "rclone", "aquacrop-x.sql.gz")
    rc.cryptcheck(tmp_path, ["aquacrop-x.sql.gz"])
    argv = [json.loads(x) for x in log.read_text().splitlines()]
    assert argv[1][:3] == ["copyto", str(tmp_path / "rclone"), "aquacrop-crypt:greenhouse/backups/aquacrop-x.sql.gz"]
    assert argv[2][0] == "cryptcheck" and "--one-way" in argv[2]
    for sub in ("sync", "bisync", "move", "delete", "purge", "config"):
        with pytest.raises(ValueError):
            rc._run(sub)
    with pytest.raises(RcloneError) as ei:
        Rclone(str(stub), "c", "aquacrop-onedrive:").check_crypt()
    assert ei.value.code == "NOT_ENCRYPTED"


@pytest.mark.skipif(shutil.which("rclone") is None and not os.environ.get("AQUACROP_REQUIRE_RCLONE"),
                    reason="rclone not installed (CI installs it on Linux and requires this test)")
def test_real_rclone_crypt_round_trip(env, tmp_path):
    """The real rclone binary against a local crypt remote: copyto, cryptcheck, listing, download and
    the drill. Proves the commands and the encryption at rest; not the owner's OneDrive account."""
    import subprocess
    store = tmp_path / "plain-remote"
    store.mkdir()
    conf = tmp_path / "rclone.conf"
    obscure = lambda s: subprocess.run(["rclone", "obscure", s], capture_output=True, text=True,  # noqa: E731
                                       check=True).stdout.strip()
    conf.write_text(f"[aquacrop-local]\ntype = local\n\n[aquacrop-crypt]\ntype = crypt\nremote = aquacrop-local:{store}\n"
                    f"password = {obscure('test-only-password')}\npassword2 = {obscure('test-only-salt')}\n")
    rc = Rclone("rclone", str(conf), "aquacrop-crypt:")
    off = Offsite(env.cfg, env.state, env.db, probe=env.probe, rclone=rc, now=env.clock)
    r = off.backup()
    name = r["backup"]["file"]
    assert r["upload"]["uploaded"] == [name], r
    stored = [p for p in store.rglob("*") if p.is_file()]
    assert stored and not any("aquacrop" in p.name for p in stored)               # names encrypted
    gz = (env.bdir / name).read_bytes()
    assert all(gz[:64] not in p.read_bytes() for p in stored)                     # contents encrypted
    assert {e["name"] for e in rc.listing()} >= {name}
    rep = off.drill()
    assert rep["ok"] and rep["checksum_ok"], rep


@pytest.mark.skipif(shutil.which("rclone") is None and not os.environ.get("AQUACROP_REQUIRE_RCLONE"),
                    reason="rclone not installed (CI installs it on Linux and requires this test)")
def test_bk07_replacement_controller_recovers_with_only_the_escrowed_keys(env, tmp_path):
    """BK-07: the controller, its card and its rclone.conf are lost. A replacement that has only the
    cloud folder and the two escrowed values (typed in again) lists, picks the newest copy with a
    completion receipt (a later upload that was cut off has none), downloads, decrypts, verifies and
    restores it with the commands of offsite-backups.md section 8.1, and the restored database records
    again. A mistyped value reads nothing.

    Rehearsal only: test-only keys and a local folder standing in for OneDrive (real rclone crypt). The
    owner's sealed envelope, Microsoft account recovery and re-authorization were NOT demonstrated."""
    import sqlite3
    import subprocess

    import aquacrop
    envelope = {"password": "bk07-test-only-password", "salt": "bk07-test-only-salt"}
    cloud = tmp_path / "onedrive-stand-in"
    cloud.mkdir()

    def rclone_conf(path, password, salt):
        ob = lambda s: subprocess.run(["rclone", "obscure", s], capture_output=True, text=True,  # noqa: E731
                                      check=True).stdout.strip()
        path.write_text(f"[aquacrop-onedrive]\ntype = local\n\n[aquacrop-crypt]\ntype = crypt\n"
                        f"remote = aquacrop-onedrive:{cloud}\npassword = {ob(password)}\n"
                        f"password2 = {ob(salt)}\n")
        path.chmod(0o600)
        return path

    # the old controller: daily backup, uploaded and cryptchecked
    old_conf = rclone_conf(tmp_path / "old-rclone.conf", envelope["password"], envelope["salt"])
    off = Offsite(env.cfg, env.state, env.db, probe=env.probe, rclone=Rclone("rclone", str(old_conf), "aquacrop-crypt:"),
                  now=env.clock)
    r = off.backup()
    name = r["backup"]["file"]
    assert r["upload"]["uploaded"] == [name], r
    sha = off.store.ledger()[-1]["sha256"]

    class CutOff(Rclone):                     # the next day's upload loses the connection before its receipt
        def cryptcheck(self, local_dir, names):
            raise RcloneError("connection lost", code="OFFLINE")
    env.clock.t += 86400
    cut = Offsite(env.cfg, env.state, env.db, probe=env.probe, rclone=CutOff("rclone", str(old_conf), "aquacrop-crypt:"),
                  now=env.clock).backup()
    interrupted = cut["backup"]["file"]
    assert cut["upload"]["failed"] == [interrupted] and interrupted > name
    in_cloud = sorted((p.relative_to(cloud).as_posix(), p.stat().st_size) for p in cloud.rglob("*") if p.is_file())
    events = env.hist.query("SELECT count(*) FROM events WHERE kind='TEST_EVENT'")[0][0]
    assert events == 20
    # lost: database, card and rclone.conf
    env.hist.close()
    for gone in (env.state, env.sd):
        shutil.rmtree(gone)
    old_conf.unlink()

    new = tmp_path / "replacement"
    new.mkdir()
    environ = {k: v for k, v in os.environ.items() if not k.startswith("RCLONE_")} | {
        "HOME": str(new), "PYTHONUTF8": "1", "PYTHONPATH": str(Path(aquacrop.__file__).resolve().parents[1])}

    def run(*cmd):
        return subprocess.run(list(cmd), cwd=new, env=environ, capture_output=True, text=True, timeout=300)

    # a mistyped envelope: nothing can be listed or downloaded
    bad = rclone_conf(new / "mistyped.conf", envelope["password"], envelope["salt"] + "x")
    assert run("rclone", "--config", str(bad), "lsjson", "aquacrop-crypt:backups").returncode != 0
    assert run("rclone", "--config", str(bad), "copyto", f"aquacrop-crypt:backups/{name}", "x.gz").returncode != 0
    assert not (new / "x.gz").exists()
    # the envelope as printed: list, download backup and manifest
    conf = str(rclone_conf(new / "rclone.conf", envelope["password"], envelope["salt"]))
    ls = run("rclone", "--config", conf, "lsjson", "aquacrop-crypt:backups")
    assert ls.returncode == 0, ls.stderr
    listed = {e["Name"] for e in json.loads(ls.stdout)}
    assert {interrupted, backup_mod.manifest_path(Path(interrupted)).name} <= listed     # there, but incomplete
    complete = [n for n in listed if n.endswith(".sql.gz") and offsite_mod.receipt_name(n) in listed]
    assert max(complete) == name
    man, rcpt = backup_mod.manifest_path(Path(name)).name, offsite_mod.receipt_name(name)
    (new / "dl").mkdir()
    for f in (name, man, rcpt):
        cp = run("rclone", "--config", conf, "copyto", f"aquacrop-crypt:backups/{f}", f"dl/{f}")
        assert cp.returncode == 0, cp.stderr
    receipt = json.loads((new / "dl" / rcpt).read_text())
    assert hashlib.sha256((new / "dl" / name).read_bytes()).hexdigest() == receipt["sha256"] == sha
    py = [sys.executable, "-m", "aquacrop"]
    v = run(*py, "backup", "verify", "--backup", f"dl/{name}", "--full", "--scratch-dir", "scratch")
    assert v.returncode == 0 and json.loads(v.stdout)["ok"] is True, v.stdout + v.stderr
    restored = new / "var" / env.db.name
    fresh = Historian(restored, env.cfg, "s-fresh-install")          # step 1: the installed, now stopped, instance
    fresh.close()
    rs = run(*py, "backup", "restore", "--backup", f"dl/{name}", "--target", str(restored), "--maintenance",
             "--config", "demo-24", "--state-dir", "var")
    assert rs.returncode == 0, rs.stdout + rs.stderr
    con = sqlite3.connect(f"file:{restored}?mode=ro", uri=True)
    try:
        assert con.execute("SELECT count(*) FROM events WHERE kind='TEST_EVENT'").fetchone()[0] == events
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        con.close()
    h2 = Historian(restored, env.cfg, "s-replacement")               # the new instance records again
    try:
        h2.event("TEST_EVENT", {"i": "after-recovery"}, actor="t")
        h2.wait_idle()
        assert h2.query("SELECT count(*) FROM events WHERE kind='TEST_EVENT'")[0][0] == events + 1
    finally:
        h2.close()
    # the replacement built nothing from the old controller and wrote nothing into the cloud folder
    assert not env.state.exists() and not env.sd.exists() and not old_conf.exists()
    assert sorted((p.relative_to(cloud).as_posix(), p.stat().st_size) for p in cloud.rglob("*")
                  if p.is_file()) == in_cloud
    assert not any("aquacrop" in p.name for p in cloud.rglob("*"))           # names encrypted at rest


def test_api_shows_state_and_policy_confirmation_is_admin_only(tmp_path, cfg48):
    from fastapi.testclient import TestClient

    from tests.integration.test_api import ORIGIN, PW, _app, login
    with TestClient(app := _app(tmp_path, cfg48), base_url=ORIGIN) as c:
        login(c, "view1", PW["view1"])
        assert c.get("/api/offsite").status_code == 403                  # 0.7.0: administrators only
        login(c, "admin1", PW["admin1"])
        v = c.get("/api/offsite").json()
        assert v["enabled"] is False and "Not activated" in v["activation"]["note"]
        assert c.get("/api/backup").json()["off_device"].startswith("NOT ACTIVATED")
        pol = v["policy"]
        assert (pol["local_daily"], pol["cloud_daily"], pol["cloud_monthly"]) == (7, 30, 12) and not pol["active"]
        body = {"policy_hash": pol["policy_hash"], "confirm": True}
        op_h = login(c, "oper1", PW["oper1"])
        assert c.post("/api/offsite/policy", json=body, headers=op_h).status_code == 403
        adm = login(c, "admin1", PW["admin1"])
        r = c.post("/api/offsite/policy", json=body | {"policy_hash": "0" * 16}, headers=adm)
        assert r.status_code == 409 and r.json()["detail"]["policy"]["policy_hash"] == pol["policy_hash"]
        assert c.post("/api/offsite/policy", json=body | {"confirm": False}, headers=adm).status_code == 422
        r = c.post("/api/offsite/policy", json=body, headers=adm)
        assert r.status_code == 200 and r.json()["confirmed"]["confirmed_by"] == "admin1"
        assert c.get("/api/offsite").json()["policy"]["active"] is True
        app.state.hist.wait_idle()
        ev = app.state.hist.query("SELECT actor, payload_json FROM events WHERE kind='OFFSITE_POLICY_CONFIRMED'")
        assert len(ev) == 1 and ev[0][0] == "admin1"


def test_linux_mountinfo_parsing(tmp_path):
    """The real probe reads the visible mount entry (last one wins) and unescapes the mount path."""
    from aquacrop.historian.sdcard import VolumeProbe
    mi = tmp_path / "mountinfo"
    mi.write_text("22 1 179:2 / / rw,relatime shared:1 - ext4 /dev/mmcblk0p2 rw\n"
                  "40 22 8:1 / /media/aquacrop\\040sd ro,nosuid shared:9 - ext4 /dev/sda1 ro\n"
                  "41 22 8:1 / /media/aquacrop\\040sd rw,nosuid,nodev,noexec,noatime shared:9 - ext4 /dev/sda1 rw\n")
    pr = VolumeProbe()
    pr.mountinfo = str(mi)
    e = pr.mount_entry(Path("/media/aquacrop sd"))
    assert e["fstype"] == "ext4" and "rw" in e["options"] and e["source"] == "/dev/sda1"
    assert pr.mount_entry(Path("/media/other")) is None
    assert pr.device_for_uuid("0000-NOPE") is None
