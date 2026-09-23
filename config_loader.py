from __future__ import annotations

import copy
from pathlib import Path
import yaml
import math


def load_config(path: str) -> dict:
    with Path(path).open("r", encoding="utf-8") as handle:
        cfg = yaml.safe_load(handle)

    devices = cfg.get("devices", {})
    resolving: set[str] = set()

    def resolve(name: str) -> dict:
        item = devices[name]
        parent = item.get("inherit")
        if not parent:
            return copy.deepcopy(item)
        if name in resolving:
            raise ValueError(f"Héritage circulaire pour {name}")
        resolving.add(name)
        base = resolve(parent)
        resolving.remove(name)
        base.update({k: copy.deepcopy(v) for k, v in item.items() if k != "inherit"})
        return base

    cfg["devices"] = {name: resolve(name) for name in devices}
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
