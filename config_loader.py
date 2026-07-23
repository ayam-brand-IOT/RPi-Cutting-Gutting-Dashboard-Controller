from __future__ import annotations

import copy
from pathlib import Path
import yaml


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
    return cfg
