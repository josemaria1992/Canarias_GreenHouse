"""Persistent alarm lifecycle (brief §8).

Identity is `rule_id + target`; the current value is an attribute, never part of the identity.
Lifecycle of one occurrence:

    raised (active) -> [value updates] -> cleared (condition gone)      \
                    -> acknowledged (by user, any time)                  > closed when all apply
                    -> reset (only if latched, only after cleared+ack)  /

Acknowledgement never clears a condition and never resets controller lockout by itself.
If the condition returns while an occurrence is still open, the same occurrence becomes active again.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Callable

from ..config.schema import AlarmRule, InstallationConfig
from ..domain.model import Quality, Sample, new_id, utcnow

GOOD = (Quality.GOOD, Quality.OVERRIDDEN)


@dataclass
class AlarmOccurrence:
    occurrence_id: str
    rule_id: str
    target: str
    severity: str
    message: str
    first_seen_utc: datetime
    last_seen_utc: datetime
    first_sim_s: float | None
    last_sim_s: float | None
    active: bool = True
    cleared_utc: datetime | None = None
    acknowledged: bool = False
    ack_user: str | None = None
    ack_utc: datetime | None = None
    latched: bool = False
    reset_user: str | None = None
    reset_utc: datetime | None = None
    value: float | bool | None = None
    threshold: float | None = None
    quality: str | None = None
    origin: str | None = None
    config_version: str | None = None
    closed: bool = False

    @property
    def reset_required(self) -> bool:
        return self.latched and self.reset_utc is None

    def can_close(self) -> bool:
        return (not self.active) and self.acknowledged and not self.reset_required

    def to_dict(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        d["reset_required"] = self.reset_required
        return d


@dataclass
class _Pending:
    since_s: float


class AlarmEngine:
    def __init__(self, cfg: InstallationConfig, *, emit: Callable[[str, dict], None] | None = None):
        self.cfg = cfg
        self.rules: list[AlarmRule] = list(cfg.alarm_rules)
        self.open: dict[tuple[str, str], AlarmOccurrence] = {}
        self.closed_recent: list[AlarmOccurrence] = []
        self.dirty: set[str] = set()
        self._pending_raise: dict[tuple[str, str], _Pending] = {}
        self._sink = emit or (lambda kind, payload: None)
        self.transitioned = False   # a raise/clear/ack/reset/close happened since the last persist

    def _emit(self, kind: str, payload: dict) -> None:
        self.transitioned = True
        self._sink(kind, payload)

    def take_transitioned(self) -> bool:
        """True once after any state transition: callers persist occurrences immediately on transitions
        and only refresh still-active ones (last_seen/value) at the snapshot interval."""
        t, self.transitioned = self.transitioned, False
        return t

    # ------------------------------------------------------------------ restore
    def restore(self, occurrences: list[AlarmOccurrence]) -> None:
        """Re-load open occurrences after restart. Conditions are re-evaluated, not duplicated."""
        for o in occurrences:
            if not o.closed:
                self.open[(o.rule_id, o.target)] = o

    # ------------------------------------------------------------------ generic raise / clear
    def raise_or_update(self, rule_id: str, target: str, *, severity: str, message: str, sim_s: float | None,
                        latch: bool = False, value=None, threshold=None, quality=None, origin=None) -> AlarmOccurrence:
        key, now = (rule_id, target), utcnow()
        occ = self.open.get(key)
        if occ is None:
            occ = AlarmOccurrence(new_id("alm"), rule_id, target, severity, message, now, now, sim_s, sim_s,
                                  latched=latch, value=value, threshold=threshold, quality=quality, origin=origin,
                                  config_version=self.cfg.config_version)
            self.open[key] = occ
            self._emit("ALARM_RAISED", occ.to_dict())
        else:
            reactivated = not occ.active
            occ.active, occ.cleared_utc = True, None
            occ.last_seen_utc, occ.last_sim_s = now, sim_s
            occ.value, occ.quality, occ.message = value, quality, message
            if latch and not occ.latched:
                occ.latched, occ.reset_utc, occ.reset_user = True, None, None
            if reactivated:
                # Condition returned on an open occurrence: needs a fresh acknowledgement.
                occ.acknowledged, occ.ack_user, occ.ack_utc = False, None, None
                self._emit("ALARM_REACTIVATED", occ.to_dict())
        self.dirty.add(occ.occurrence_id)
        return occ

    def clear(self, rule_id: str, target: str) -> None:
        occ = self.open.get((rule_id, target))
        if occ is None or not occ.active:
            return
        occ.active, occ.cleared_utc = False, utcnow()
        self.dirty.add(occ.occurrence_id)
        self._emit("ALARM_CLEARED", occ.to_dict())
        self._maybe_close(occ)

    def is_active(self, rule_id: str, target: str) -> bool:
        occ = self.open.get((rule_id, target))
        return bool(occ and occ.active)

    def get(self, occurrence_id: str) -> AlarmOccurrence | None:
        for o in self.open.values():
            if o.occurrence_id == occurrence_id:
                return o
        return None

    # ------------------------------------------------------------------ operator actions
    def acknowledge(self, occurrence_id: str, user: str) -> AlarmOccurrence:
        occ = self.get(occurrence_id)
        if occ is None:
            raise KeyError(occurrence_id)
        if not occ.acknowledged:
            occ.acknowledged, occ.ack_user, occ.ack_utc = True, user, utcnow()
            self.dirty.add(occ.occurrence_id)
            self._emit("ALARM_ACKNOWLEDGED", occ.to_dict())
            self._maybe_close(occ)
        return occ

    def reset(self, occurrence_id: str, user: str) -> AlarmOccurrence:
        occ = self.get(occurrence_id)
        if occ is None:
            raise KeyError(occurrence_id)
        if occ.active:
            raise ValueError("condition still active; cannot reset")
        if not occ.acknowledged:
            raise ValueError("acknowledge before reset")
        if occ.latched and occ.reset_utc is None:
            occ.reset_user, occ.reset_utc = user, utcnow()
            self.dirty.add(occ.occurrence_id)
            self._emit("ALARM_RESET", occ.to_dict())
        self._maybe_close(occ)
        return occ

    def _maybe_close(self, occ: AlarmOccurrence) -> None:
        if occ.can_close():
            occ.closed = True
            self.open.pop((occ.rule_id, occ.target), None)
            self.closed_recent.append(occ)
            del self.closed_recent[:-200]
            self.dirty.add(occ.occurrence_id)
            self._emit("ALARM_CLOSED", occ.to_dict())

    # ------------------------------------------------------------------ configured point rules
    def evaluate_samples(self, samples: dict[str, Sample], t_s: float, sim_s: float | None) -> None:
        for rule in self.rules:
            s = samples.get(rule.point)
            if s is None or s.quality not in GOOD or s.value is None:
                continue  # unknown quality: neither raise nor clear (quality alarms cover it)
            key = (rule.rule_id, rule.point)
            occ = self.open.get(key)
            active_now = occ is not None and occ.active
            if rule.kind == "HIGH":
                cond = s.value > rule.threshold if not active_now else s.value > rule.threshold - rule.hysteresis
            elif rule.kind == "LOW":
                cond = s.value < rule.threshold if not active_now else s.value < rule.threshold + rule.hysteresis
            elif rule.kind == "DI_INACTIVE":
                cond = not bool(s.value)
            else:
                cond = bool(s.value)
            if cond and not active_now:
                pend = self._pending_raise.setdefault(key, _Pending(t_s))
                if t_s - pend.since_s + 1e-9 < rule.debounce_s:
                    continue
                self._pending_raise.pop(key, None)
                self.raise_or_update(rule.rule_id, rule.point, severity=rule.severity, latch=rule.latch,
                                     message=f"{rule.rule_id} on {rule.point}", sim_s=sim_s, value=s.value,
                                     threshold=rule.threshold, quality=s.quality.value, origin=s.origin.value)
            elif cond:
                occ.last_seen_utc, occ.last_sim_s, occ.value = utcnow(), sim_s, s.value
                self.dirty.add(occ.occurrence_id)
            else:
                self._pending_raise.pop(key, None)
                if active_now:
                    self.clear(rule.rule_id, rule.point)

        # Automatic signal-quality rules per AI point with a scaling profile, per device comm.
        for pid, s in samples.items():
            if s.raw_unit in ("uA", "mV") and s.calibration_ref:
                if s.quality == Quality.INVALID:
                    self.raise_or_update("AI_SIGNAL_INVALID", pid, severity="WARNING", sim_s=sim_s,
                                         message=f"{pid}: {s.reason}", value=s.raw_value,
                                         quality=s.quality.value, origin=s.origin.value)
                elif s.quality in GOOD:
                    self.clear("AI_SIGNAL_INVALID", pid)

    # ------------------------------------------------------------------ equipment item limits (0.7.0, ALM-01)
    def evaluate_item_limits(self, inputs: list[dict], t_s: float, sim_s: float | None) -> None:
        """Each input: {"item", "name", "role", "label", "unit", "value" (engineering, None = UNKNOWN), "low",
        "high", "deadband", "delay_s", "quality"}. A limit raises after it has been passed for `delay_s`; it clears
        only once the value is back inside by the deadband. UNKNOWN neither raises nor clears: the communication
        and signal alarms say why, and a fault that can no longer be read is not called fixed."""
        for x in inputs:
            target = f"{x['item']}.{x['role']}"
            for kind in ("LOW", "HIGH"):
                lim = x.get(kind.lower())
                rule = f"ITEM_{kind}"
                key = (rule, target)
                if lim is None:
                    self._pending_raise.pop(key, None)
                    if self.is_active(rule, target):
                        self.clear(rule, target)       # the limit was removed from the item
                    continue
                v = x.get("value")
                if v is None:
                    continue
                active = self.is_active(rule, target)
                db = x.get("deadband") or 0.0
                cond = (v < lim if not active else v < lim + db) if kind == "LOW" else (v > lim if not active else v > lim - db)
                if cond and not active:
                    pend = self._pending_raise.setdefault(key, _Pending(t_s))
                    if t_s - pend.since_s + 1e-9 < (x.get("delay_s") or 0.0):
                        continue
                    self._pending_raise.pop(key, None)
                    word = "below its low limit" if kind == "LOW" else "above its high limit"
                    self.raise_or_update(rule, target, severity="WARNING", sim_s=sim_s, value=v, threshold=lim,
                                         quality=x.get("quality"), origin=x.get("origin"),
                                         message=f"{x['name']} {x['label']} is {v:g} {x.get('unit') or ''}, {word} "
                                                 f"{lim:g}. Response: alarm only (programs use their own stop "
                                                 f"conditions). Check: the reading and the equipment on site.")
                elif cond:
                    occ = self.open[key]
                    occ.last_seen_utc, occ.last_sim_s, occ.value = utcnow(), sim_s, v
                    self.dirty.add(occ.occurrence_id)
                else:
                    self._pending_raise.pop(key, None)
                    if active:
                        self.clear(rule, target)

    def evaluate_devices(self, device_ok: dict[str, tuple[bool, str | None]], sim_s: float | None) -> None:
        for uid, (ok, err) in device_ok.items():
            if ok:
                self.clear("DEVICE_COMM", uid)
            else:
                self.raise_or_update("DEVICE_COMM", uid, severity="WARNING", sim_s=sim_s,
                                     message=f"{uid}: {err}")

    def active_blocking(self) -> list[AlarmOccurrence]:
        """Occurrences that block AUTO precheck: critical-and-active, or latched-awaiting-reset."""
        return [o for o in self.open.values() if (o.active and o.severity == "CRITICAL") or o.reset_required]

    def take_dirty(self) -> list[AlarmOccurrence]:
        ids, self.dirty = self.dirty, set()
        by_id = {o.occurrence_id: o for o in list(self.open.values()) + self.closed_recent}
        return [by_id[i] for i in sorted(ids) if i in by_id]

