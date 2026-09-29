from __future__ import annotations

import copy
import json
import os
import threading
import time
from pathlib import Path
from typing import Any


class StateStore:
    def __init__(self, device_names: list[str], default_breaks=None, settings_path=None):
        self._lock = threading.RLock()
        self._settings_path = Path(settings_path) if settings_path else None
        runtime = {
            "breaks": list(default_breaks or ["09:00", "12:00", "15:00", "18:00"]),
            "counter_offsets": {},
            "productivity_setpoint": 0,
            "reset_sequence": 0,
            "reset_at": None,
        }
        if self._settings_path and self._settings_path.exists():
            try:
                saved = json.loads(self._settings_path.read_text(encoding="utf-8"))
                if isinstance(saved, dict):
                    runtime.update(saved)
            except Exception as error:
                print(f"[STATE] paramètres runtime ignorés: {error}", flush=True)
        self._data: dict[str, Any] = {
            "timestamp": time.time(),
            "rpi": {
                "gpio": {}, "cip": {}, "gpio_error": "", "mqtt_connected": False,
                **runtime,
            },
            "weather": {"temperature_c": None, "condition": "unknown"},
            "devices": {
                name: {"connected": False, "values": {}, "parameters": {},
                       "error": "initialisation", "parameter_error": ""}
                for name in device_names
            },
            "last_command": {},
        }

    def _save_runtime(self):
        if not self._settings_path:
            return
        payload = {
            key: copy.deepcopy(self._data["rpi"].get(key))
            for key in ("breaks", "counter_offsets", "reset_sequence", "reset_at", "productivity_setpoint")
        }
        self._settings_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._settings_path.with_suffix(self._settings_path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temporary, self._settings_path)

    def update_device(self, name: str, connected: bool, values=None, error=""):
        with self._lock:
            device = self._data["devices"][name]
            device["connected"] = connected
            device["error"] = error
            device["last_update"] = time.time()
            if values is not None:
                device["values"] = values
            self._data["timestamp"] = time.time()

    def update_gpio(self, values: dict[str, bool], cip=None, people_counts=None):
        with self._lock:
            self._data["rpi"]["gpio"] = dict(values)
            if people_counts is not None:
                counts = dict(people_counts)
                self._data["rpi"]["people_gpio"] = counts
                self._data["rpi"]["people_gpio_total"] = (
                    sum(counts.values()) if all(counts.get(side) is not None
                                               for side in ("left", "right")) else None
                )
            if cip is not None:
                self._data["rpi"]["cip"] = copy.deepcopy(cip)
            self._data["rpi"]["gpio_error"] = ""
            self._data["timestamp"] = time.time()

    def update_parameters(self, name: str, parameters=None, error=""):
        with self._lock:
            device = self._data["devices"][name]
            if parameters is not None:
                device["parameters"] = dict(parameters)
            device["parameter_error"] = error
            self._data["timestamp"] = time.time()

    def set_gpio_error(self, error: str):
        with self._lock:
            self._data["rpi"]["gpio_error"] = error
            if "people_gpio" in self._data["rpi"]:
                self._data["rpi"]["people_gpio"] = {"left": None, "right": None}
                self._data["rpi"]["people_gpio_total"] = None
            self._data["timestamp"] = time.time()

    def set_mqtt_connected(self, connected: bool):
        with self._lock:
            self._data["rpi"]["mqtt_connected"] = connected

    def update_weather(self, temperature_c=None, condition="unknown"):
        with self._lock:
            self._data["weather"] = {
                "temperature_c": temperature_c,
                "condition": str(condition or "unknown").lower(),
                "last_update": time.time(),
            }
            self._data["timestamp"] = time.time()

    def update_people_count(self, value):
        with self._lock:
            self._data["rpi"]["people_count"] = max(0, int(value))
            self._data["rpi"].pop("people_by_machine", None)
            self._data["timestamp"] = time.time()

    def update_people_counts(self, left, right):
        """Store one complete per-machine reading and its total atomically."""
        counts = {"left": left, "right": right}
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 0
               for v in counts.values()):
            raise ValueError("left and right must be non-negative integers")
        with self._lock:
            self._data["rpi"]["people_by_machine"] = counts
            self._data["rpi"]["people_count"] = left + right
            self._data["timestamp"] = time.time()

    def update_productivity_setpoint(self, value):
        """Shared visual target in fish/min; zero hides the target line."""
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 10000 or int(value) != value:
            raise ValueError("productivity_setpoint: integer from 0 to 10000 fish/min required")
        with self._lock:
            previous = self._data["rpi"]["productivity_setpoint"]
            self._data["rpi"]["productivity_setpoint"] = int(value)
            try:
                self._save_runtime()
            except Exception:
                self._data["rpi"]["productivity_setpoint"] = previous
                raise
            self._data["timestamp"] = time.time()

    def update_breaks(self, breaks):
        with self._lock:
            self._data["rpi"]["breaks"] = list(breaks)
            self._data["timestamp"] = time.time()
            self._save_runtime()

    def reset_data(self):
        with self._lock:
            offsets = {}
            for name, device in self._data["devices"].items():
                values = device.get("values", {})
                selected = {
                    key: int(values[key])
                    for key in ("fish_counter", "ejected_fish", "ejector_count")
                    if key in values
                }
                if selected:
                    offsets[name] = selected
            self._data["rpi"]["counter_offsets"] = offsets
            self._data["rpi"]["reset_sequence"] = (
                int(self._data["rpi"].get("reset_sequence", 0)) + 1
            )
            self._data["rpi"]["reset_at"] = time.time()
            self._data["timestamp"] = time.time()
            self._save_runtime()

    def update_command(self, payload: dict):
        with self._lock:
            self._data["last_command"] = copy.deepcopy(payload)
            self._data["timestamp"] = time.time()

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(self._data)
