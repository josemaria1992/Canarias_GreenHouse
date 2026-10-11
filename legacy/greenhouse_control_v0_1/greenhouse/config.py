"""Process configuration: intentionally fail closed for missing secrets and hardware IDs."""
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    backend: str = "simulation"
    db_path: str = "./data/greenhouse.sqlite3"
    poll_seconds: float = 3.0
    username: str = "operator"
    password: str = ""
    serial_port: str = ""
    relay_id: int = 0
    analog_id: int = 0
    baudrate: int = 9600
    parity: str = "N"
    stopbits: int = 1
    bench_writes: bool = False

    @classmethod
    def from_env(cls):
        def number(name, fallback):
            return int(os.environ.get(name, fallback))
        config = cls(
            backend=os.environ.get("HYDRO_BACKEND", "simulation").strip().lower(),
            db_path=os.environ.get("HYDRO_DB", "./data/greenhouse.sqlite3"),
            poll_seconds=float(os.environ.get("HYDRO_POLL_SECONDS", "3")),
            username=os.environ.get("HYDRO_USER", "operator"),
            password=os.environ.get("HYDRO_PASSWORD", ""),
            serial_port=os.environ.get("HYDRO_SERIAL_PORT", ""),
            relay_id=number("HYDRO_RELAY_ID", 0),
            analog_id=number("HYDRO_ANALOG_ID", 0),
            baudrate=number("HYDRO_BAUDRATE", 9600),
            parity=os.environ.get("HYDRO_PARITY", "N"),
            stopbits=number("HYDRO_STOPBITS", 1),
            bench_writes=os.environ.get("HYDRO_BENCH_WRITES", "0") == "1",
        )
        config.validate()
        return config

    def validate(self):
        if not self.password or len(self.password) < 12:
            raise ValueError("HYDRO_PASSWORD is required and must be >=12 characters")
        if self.backend not in ("simulation", "modbus"):
            raise ValueError("HYDRO_BACKEND must be simulation or modbus")
        if not 0.5 <= self.poll_seconds <= 300:
            raise ValueError("HYDRO_POLL_SECONDS must be between 0.5 and 300")
        if self.backend == "modbus":
            if not self.serial_port:
                raise ValueError("HYDRO_SERIAL_PORT is required for real Modbus")
            if not (1 <= self.relay_id <= 247 and 1 <= self.analog_id <= 247):
                raise ValueError("Real Modbus device IDs must be explicit and in range 1..247")
            if self.relay_id == self.analog_id:
                raise ValueError("Both modules on the same RS485 bus need DIFFERENT device IDs")
        if self.parity not in ("N", "E", "O") or self.stopbits not in (1, 2):
            raise ValueError("Invalid serial parameters")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
