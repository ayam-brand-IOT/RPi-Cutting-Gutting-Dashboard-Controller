from __future__ import annotations

import copy
from pathlib import Path
import yaml
import math


def load_config(path: str) -> dict:
    with Path(path).open("r", encoding="utf-8") as handle:
        cfg = yaml.safe_load(handle)

    devices = cfg.get("devices", {})
    profiles = cfg.get("device_profiles", {})
    resolving: set[str] = set()

    def resolve(name: str) -> dict:
        item = devices[name]
        parent = item.get("inherit")
        if name in resolving:
            raise ValueError(f"Héritage circulaire pour {name}")
        resolving.add(name)
        base = resolve(parent) if parent else {}
        resolving.remove(name)
        profile = item.get("profile")
        if profile:
            if profile not in profiles:
                raise ValueError(f"Profil inconnu pour {name}: {profile}")
            base.update(copy.deepcopy(profiles[profile]))
        base.update({k: copy.deepcopy(v) for k, v in item.items() if k != "inherit"})
        return base

    cfg["devices"] = {name: resolve(name) for name in devices}
    for name, device in cfg["devices"].items():
        for section in ("input_registers", "telemetry_holding_registers", "holding_registers", "coils"):
            registers = device.get(section, {})
            if not isinstance(registers, dict):
                raise ValueError(f"devices.{name}.{section}: dictionnaire de registres attendu")
            for key, spec in registers.items():
                address = spec.get("address") if isinstance(spec, dict) else spec
                if isinstance(address, bool) or not isinstance(address, int) or not 0 <= address <= 65535:
                    raise ValueError(
                        f"devices.{name}.{section}.{key}: adresse entière 0–65535 requise; "
                        "vérifier l'indentation YAML"
                    )
    # CIP register addresses/keys remain in native Modbus milliseconds.
    # Human-facing bounds in YAML are explicitly specified in seconds.
    for device in cfg["devices"].values():
        for key in ("cip_on_ms", "cip_off_ms"):
            spec = device.get("holding_registers", {}).get(key)
            if spec is None:
                continue
            for bound in ("min", "max"):
                if bound + "_s" in spec:
                    if bound in spec:
                        raise ValueError(f"{key}: specify {bound}_s or {bound}, not both")
                    raw = spec.pop(bound + "_s")
                    seconds = float(raw)
                    if isinstance(raw, bool) or not math.isfinite(seconds) or not 0.1 <= seconds <= 60:
                        raise ValueError("CIP bounds must be 0.1–60 seconds")
                    ms = round(seconds * 1000)
                    if abs(seconds * 1000 - ms) > 0.000001:
                        raise ValueError("CIP seconds support at most 3 decimal places")
                    spec[bound] = ms
    return cfg
