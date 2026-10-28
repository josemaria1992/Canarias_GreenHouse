"""Simulated modules of the digital twin (0.7.0, MOD-01): modules that exist only in the twin's workspace.

A simulated module is one of the supported catalog models (Relay (D), IO 8CH, Analog Input 8CH) on the twin's own
virtual bus `sim_virtual`. It adds channels to the twin's configuration only: the real system never sees it, no
serial library is involved, and a twin channel binding on it can never be exported (the export says why). Adding or
removing one rebuilds the twin's configuration in a controlled reset; channel bindings to a removed module stay
recorded but have no channel.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..config.catalog import ModelId

BUS = "sim_virtual"
SKU = {ModelId.WAVESHARE_RELAY_D.value: "26517", ModelId.WAVESHARE_IO_8CH.value: "26244",
       ModelId.WAVESHARE_AI_8CH.value: "25821"}
WORDS = {ModelId.WAVESHARE_RELAY_D.value: "Relay (D): 8 relay outputs, 8 inputs",
         ModelId.WAVESHARE_IO_8CH.value: "IO 8CH: 8 transistor outputs, 8 inputs",
         ModelId.WAVESHARE_AI_8CH.value: "Analog Input 8CH: 8 inputs, 4-20 mA"}


class VirtualRefused(ValueError):
    pass


class VirtualModules:
    def __init__(self, path: Path):
        self.path = Path(path)

    def all(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8")).get("modules", [])
        except (OSError, ValueError):
            return []

    def _save(self, mods: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps({"modules": mods}, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, device_uid: str, model: str, label: str, live_uids: set[str]) -> dict:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", device_uid or ""):
            raise VirtualRefused("a module name: lower-case letters, digits or _, starting with a letter")
        if model not in SKU:
            raise VirtualRefused("choose a supported module model")
        mods = self.all()
        if device_uid in live_uids or any(m["device_uid"] == device_uid for m in mods):
            raise VirtualRefused(f"{device_uid} already exists")
        if len(mods) >= 16:
            raise VirtualRefused("at most 16 simulated modules")
        addr = max([m["address"] for m in mods] + [100]) + 1
        dev = {"device_uid": device_uid, "bus_id": BUS, "address": addr, "model": model, "sku": SKU[model],
               "label": (label or f"{device_uid} (simulated)")[:80]}
        if model == ModelId.WAVESHARE_AI_8CH.value:
            dev["hardware_variant"] = "STANDARD"
            dev["ai"] = {str(i): {"mode": "3"} for i in range(1, 9)}
        self._save(mods + [dev])
        return dev

    def remove(self, device_uid: str) -> None:
        mods = self.all()
        if not any(m["device_uid"] == device_uid for m in mods):
            raise VirtualRefused(f"no simulated module {device_uid}")
        self._save([m for m in mods if m["device_uid"] != device_uid])

    def merge(self, cfg_doc: dict) -> dict:
        """The twin's configuration document with the simulated modules and their bus added."""
        mods = self.all()
        if not mods:
            return cfg_doc
        d = dict(cfg_doc)
        d["buses"] = [b for b in d["buses"] if b["bus_id"] != BUS] + [{"bus_id": BUS, "kind": "virtual", "poll_interval_s": 1.0}]
        d["devices"] = [x for x in d.get("devices", []) if x.get("bus_id") != BUS] + mods
        return d

    def uids(self) -> set[str]:
        return {m["device_uid"] for m in self.all()}
