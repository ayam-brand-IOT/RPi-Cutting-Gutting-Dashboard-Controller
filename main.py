#!/usr/bin/env python3
from __future__ import annotations

import argparse
import signal
import threading
from pathlib import Path

from config_loader import load_config
from dashboard import run_dashboard
from gpio_manager import GPIOManager
from modbus_manager import ModbusManager
from mqtt_manager import MQTTManager
from state import StateStore
from weather_manager import WeatherManager


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--windowed", action="store_true")
    parser.add_argument("--dashboard", choices=("1", "2", "old", "light", "cycle"), default="cycle")
    parser.add_argument("--no-mqtt", action="store_true")
    args = parser.parse_args()

    dashboard_runner = run_dashboard
    if args.dashboard == "2":
        from dashboard2 import run_dashboard as dashboard_runner
    elif args.dashboard == "cycle":
        from dashboard_cycle import run_dashboard as dashboard_runner
    elif args.dashboard == "light":
        from dashboard_light import run_dashboard as dashboard_runner
    elif args.dashboard == "old":
        from dashboard_old import run_dashboard as dashboard_runner

    cfg = load_config(args.config)
    cfg["devices"] = {
        name: device
        for name, device in cfg["devices"].items()
        if device.get("enabled", True)
    }
    if args.windowed:
        cfg["dashboard"]["fullscreen"] = False
    stop_event = threading.Event()
    screenshot_event = threading.Event()
    settings_path = Path(args.config).resolve().parent / cfg["machine"].get(
        "runtime_settings_file", "runtime_settings.json"
    )
    state = StateStore(
        list(cfg["devices"]),
        cfg.get("schedule", {}).get("breaks"),
        settings_path,
    )
    modbus = ModbusManager(cfg["machine"], cfg["devices"], state, stop_event)
    gpio = None
    if cfg.get("gpio", {}).get("enabled") is True:
        gpio = GPIOManager(cfg["gpio"], state, stop_event)
    mqtt_thread = None
    weather_thread = None

    if cfg["mqtt"].get("enabled", True) and not args.no_mqtt:
        mqtt_thread = MQTTManager(cfg["mqtt"], cfg["devices"], state, modbus, gpio, stop_event)
    if cfg.get("weather", {}).get("enabled", False):
        weather_thread = WeatherManager(cfg["weather"], state, stop_event)
    def modbus_ack(payload):
        state.update_command(payload)
        if mqtt_thread:
            mqtt_thread.publish_ack(payload)

    modbus.ack_callback = modbus_ack

    def shutdown(*_):
        stop_event.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGUSR1"):
        signal.signal(signal.SIGUSR1, lambda *_: screenshot_event.set())
    modbus.start()
    # Les solénoïdes CIP sont maintenant pilotés par le CP-IO22. On conserve
    # les fonctions Waveshare dans le firmware, mais on les force à OFF au boot.
    if cfg.get("gpio", {}).get("disable_waveshare_cip_on_start", True):
        for name, device in cfg["devices"].items():
            if "cip_enable" in device.get("coils", {}):
                modbus.enqueue_write({"device": name, "parameter": "cip_enable", "value": 0})
    if gpio:
        gpio.start()
    if mqtt_thread:
        mqtt_thread.start()
    if weather_thread:
        weather_thread.start()
    try:
        dashboard_runner(cfg["dashboard"], cfg["devices"], state, stop_event, screenshot_event)
    finally:
        stop_event.set()
        for worker in (modbus, gpio, mqtt_thread, weather_thread):
            if worker:
                worker.join(timeout=3)


if __name__ == "__main__":
    main()
