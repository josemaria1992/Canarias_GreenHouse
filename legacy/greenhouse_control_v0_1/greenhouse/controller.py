"""Local state machine and alarm rules. AUTO NEVER writes to physical outputs in v0.1."""
import asyncio
import time
from dataclasses import asdict
from datetime import datetime, timezone

from .driver import SimulationDriver, WaveshareModbusDriver
from .storage import Storage, now


class Controller:
    def __init__(self, config):
        self.config = config
        self.storage = Storage(config.db_path)
        self.driver = (SimulationDriver() if config.backend == "simulation"
                       else WaveshareModbusDriver(config))
        self.lock = asyncio.Lock()
        self.mode = "MANUAL"  # Always restart conservatively; no auto-resume.
        self.phase = "IDLE"
        self.sector = None
        self.phase_started = time.monotonic()
        self.auto_config = self.storage.settings_get("auto_demo", {"run_seconds": 15, "pause_seconds": 10})
        initial = ([{"min": 4000, "max": 20000} for _ in range(8)]
                   if config.backend == "simulation" else
                   [{"min": None, "max": None} for _ in range(8)])
        self.thresholds = self.storage.settings_get("thresholds", initial)
        self.flags = {"overflow": False, "no_flow": False}
        self.current_alarms = set()
        self.latest = None
        self.last_read = None
        self.quality = "STARTING"
        self.last_error = ""
        self.running = False
        self.task = None

    def _alarm_descriptions(self, snapshot, quality):
        alarms = set()
        if quality != "OK":
            alarms.add("Communication failure")
        if self.config.backend == "simulation":
            if self.flags["overflow"]:
                alarms.add("SIMULATED tank overflow")
            if self.flags["no_flow"] and (
                    (self.mode == "AUTO" and self.phase == "IRRIGATING") or
                    "SIMULATED watering not occurring" in self.current_alarms):
                alarms.add("SIMULATED watering not occurring")
        if snapshot is not None:
            for i, value in enumerate(snapshot.analog):
                limits = self.thresholds[i]
                if (limits["min"] is not None and value < limits["min"]) or (
                    limits["max"] is not None and value > limits["max"]):
                    alarms.add(f"AI{i+1} out of configured raw range: {value}")
        return alarms

    async def poll_once(self):
        async with self.lock:
            try:
                snapshot = await asyncio.to_thread(self.driver.read)
                self.latest = snapshot
                self.quality = "OK"
                self.last_error = ""
                self.last_read = now()
            except Exception as exc:
                # Last known good value is intentionally NOT represented as live.
                self.latest = None
                self.quality = "ERROR"
                self.last_error = str(exc)[:240]
            self._advance_auto()
            new_alarms = self._alarm_descriptions(self.latest, self.quality)
            for a in sorted(new_alarms - self.current_alarms):
                self.storage.event("ALARM_RAISED", "system", a)
            for a in sorted(self.current_alarms - new_alarms):
                self.storage.event("ALARM_CLEARED", "system", a)
            self.current_alarms = new_alarms
            if new_alarms and self.mode == "AUTO":
                self.mode = "MANUAL"
                self.phase = "STOPPED_ALARM"
                self.sector = None
                self.storage.event("AUTO_STOP", "system", "; ".join(sorted(new_alarms)))
            self.storage.sample(self.latest, self.mode, self.sector, self.phase, self.quality)
            return self.state()

    def _advance_auto(self):
        if self.mode != "AUTO":
            return
        elapsed = time.monotonic() - self.phase_started
        if self.phase == "IRRIGATING" and elapsed >= self.auto_config["run_seconds"]:
            self.phase = "PAUSE"
            self.phase_started = time.monotonic()
            self.storage.event("AUTO_PAUSE", "system", "Virtual irrigation paused")
        elif self.phase == "PAUSE" and elapsed >= self.auto_config["pause_seconds"]:
            self.sector = 2 if self.sector == 1 else 1
            self.phase = "IRRIGATING"
            self.phase_started = time.monotonic()
            self.storage.event("AUTO_IRRIGATE", "system", f"Virtual sector {self.sector}")

    def state(self):
        snap = asdict(self.latest) if self.latest else None
        return {
            "backend": self.config.backend, "mode": self.mode,
            "phase": self.phase, "sector": self.sector,
            "phase_elapsed_seconds": round(time.monotonic() - self.phase_started, 1),
            "quality": self.quality, "last_read_utc": self.last_read,
            "error": self.last_error, "io": snap,
            "alarms": sorted(self.current_alarms), "auto_config": self.auto_config,
            "thresholds": self.thresholds,
            "flags": self.flags if self.config.backend == "simulation" else {},
            "bench_writes_enabled": self.config.bench_writes,
            "real_auto_locked": self.config.backend == "modbus",
        }

    async def set_mode(self, mode, actor):
        if mode not in ("MANUAL", "AUTO"):
            raise ValueError("Expected MANUAL or AUTO")
        async with self.lock:
            if mode == "AUTO" and self.config.backend != "simulation":
                raise PermissionError("Real AUTO disabled until equipment mapping and interlocks are verified")
            if mode == "AUTO" and (self.current_alarms or self.quality != "OK"):
                raise ValueError("Clear all alarms and restore communication before AUTO")
            before = self.mode
            self.mode = mode
            self.phase = "IRRIGATING" if mode == "AUTO" else "IDLE"
            self.sector = 1 if mode == "AUTO" else None
            self.phase_started = time.monotonic()
            self.storage.event("MODE", actor, f"{before} -> {mode}")
            return self.state()

    async def set_relay(self, index, on, actor):
        if not 0 <= index < 8:
            raise ValueError("Relay index out of range")
        async with self.lock:
            if self.mode != "MANUAL":
                raise PermissionError("Relay writes are only allowed in MANUAL")
            if self.config.backend == "modbus" and not self.config.bench_writes:
                raise PermissionError("Physical outputs are write-locked (HYDRO_BENCH_WRITES=0)")
            if self.quality != "OK":
                raise PermissionError("Writes locked while device communication is unhealthy")
            if self.current_alarms and on:
                raise PermissionError("Turning ON is locked while alarms are active; turning OFF remains allowed")
            await asyncio.to_thread(self.driver.write_relay, index, bool(on))
            self.storage.event("RELAY_WRITE", actor, f"DO{index+1} = {int(bool(on))}")
        return await self.poll_once()

    async def set_analog(self, index, value, actor):
        if self.config.backend != "simulation":
            raise PermissionError("Physical analog channels are read-only")
        if not 0 <= index < 8 or not 0 <= value <= 25000:
            raise ValueError("Invalid simulation channel/value")
        async with self.lock:
            self.driver.set_analog(index, value)
            self.storage.event("SIM_ANALOG", actor, f"AI{index+1}={value} uA")
        return await self.poll_once()

    async def set_input(self, index, on, actor):
        if self.config.backend != "simulation":
            raise PermissionError("Digital input emulation is simulation-only")
        if not 0 <= index < 8:
            raise ValueError("Invalid digital input")
        async with self.lock:
            self.driver.set_input(index, bool(on))
            self.storage.event("SIM_INPUT", actor, f"DI{index+1}={int(bool(on))}")
        return await self.poll_once()

    async def set_flag(self, name, active, actor):
        if self.config.backend != "simulation" or name not in self.flags:
            raise PermissionError("Only simulation alarm flags can be injected")
        async with self.lock:
            self.flags[name] = bool(active)
            self.storage.event("SIM_FAULT", actor, f"{name}={bool(active)}")
        return await self.poll_once()

    async def set_threshold(self, index, minimum, maximum, actor):
        if not 0 <= index < 8:
            raise ValueError("Invalid channel")
        if minimum is not None and (minimum < 0 or minimum > 65535):
            raise ValueError("Minimum outside unsigned register range")
        if maximum is not None and (maximum < 0 or maximum > 65535):
            raise ValueError("Maximum outside unsigned register range")
        if minimum is not None and maximum is not None and minimum >= maximum:
            raise ValueError("min must be less than max")
        async with self.lock:
            self.thresholds[index] = {"min": minimum, "max": maximum}
            self.storage.settings_set("thresholds", self.thresholds)
            self.storage.event("THRESHOLD", actor, f"AI{index+1}: {minimum}..{maximum} raw")
        return await self.poll_once()

    async def set_auto_config(self, run_seconds, pause_seconds, actor):
        if not (1 <= run_seconds <= 3600 and 1 <= pause_seconds <= 3600):
            raise ValueError("Both durations must be 1..3600 seconds")
        async with self.lock:
            self.auto_config = {"run_seconds": run_seconds, "pause_seconds": pause_seconds}
            self.storage.settings_set("auto_demo", self.auto_config)
            self.storage.event("AUTO_DEMO_CONFIG", actor, str(self.auto_config))
            return self.state()

    async def _loop(self):
        while self.running:
            await self.poll_once()
            await asyncio.sleep(self.config.poll_seconds)

    async def start(self):
        self.running = True
        await self.poll_once()
        self.task = asyncio.create_task(self._loop())
        self.storage.event("START", "system", f"Backend {self.config.backend}; started in MANUAL")

    async def stop(self):
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        self.storage.event("STOP", "system", "Software shutdown; relay states NOT modified")
        self.driver.close()
        self.storage.close()
