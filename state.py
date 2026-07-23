from __future__ import annotations

import copy
import threading
import time
from typing import Any


class StateStore:
    def __init__(self, device_names: list[str]):
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {
            "timestamp": time.time(),
            "rpi": {"gpio": {}, "cip": {}, "gpio_error": "", "mqtt_connected": False},
            "devices": {
                name: {"connected": False, "values": {}, "parameters": {},
                       "error": "initialisation", "parameter_error": ""}
                for name in device_names
            },
            "last_command": {},
        }

    def update_device(self, name: str, connected: bool, values=None, error=""):
        with self._lock:
            device = self._data["devices"][name]
            device["connected"] = connected
            device["error"] = error
            device["last_update"] = time.time()
            if values is not None:
                device["values"] = values
            self._data["timestamp"] = time.time()

    def update_gpio(self, values: dict[str, bool], cip=None):
        with self._lock:
            self._data["rpi"]["gpio"] = dict(values)
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
            self._data["timestamp"] = time.time()

    def set_mqtt_connected(self, connected: bool):
        with self._lock:
            self._data["rpi"]["mqtt_connected"] = connected

    def update_command(self, payload: dict):
        with self._lock:
            self._data["last_command"] = copy.deepcopy(payload)
            self._data["timestamp"] = time.time()

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(self._data)
