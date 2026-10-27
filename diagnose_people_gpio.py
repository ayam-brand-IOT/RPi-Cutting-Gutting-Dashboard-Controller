"""Read real people-button inputs only; never open CIP outputs."""
import argparse
import threading
import time
from pathlib import Path

from config_loader import load_config
from gpio_manager import GPIOManager, DigitalInputDevice
from state import StateStore


def monitor(config, duration=0, input_factory=None):
    factory = input_factory or DigitalInputDevice
    if factory is None:
        raise RuntimeError("gpiozero is required on the Raspberry Pi")
    inputs = {name: item for name, item in config.get("inputs", {}).items()
              if item.get("enabled", True) and item.get("people_side") in ("left", "right")}
    # Intentionally omit CIP configuration and never call GPIOManager._setup().
    manager = GPIOManager({"inputs": inputs}, StateStore([]), threading.Event())
    previous = {}
    try:
        for name, item in inputs.items():
            kwargs = {"pull_up": item.get("pull_up", True)}
            if kwargs["pull_up"] is None:
                kwargs["active_state"] = not item.get("active_low", False)
            manager.inputs[name] = factory(int(item["pin"]), **kwargs)
            print(f"GPIO{item['pin']} = {name}: {item['people_side'].upper()} "
                  f"{int(item['people_delta']):+d}", flush=True)
        print("LIVE input monitor. Release all buttons, then press GPIO8 alone. "
              "No CIP outputs are opened. Ctrl+C to exit.", flush=True)
        started = time.monotonic()
        while not duration or time.monotonic() - started < duration:
            for name, device in manager.inputs.items():
                sample = (bool(device.pin.state), bool(device.value))
                if previous.get(name) != sample:
                    previous[name] = sample
                    print(f"[RAW] GPIO{inputs[name]['pin']} {name}: level={int(sample[0])} "
                          f"pressed={int(sample[1])}", flush=True)
            manager._publish_state()
            time.sleep(0.02)
    finally:
        for device in manager.inputs.values():
            device.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--duration", type=float, default=0, help="Seconds; 0 runs until Ctrl+C")
    args = parser.parse_args()
    if args.duration < 0:
        parser.error("duration must be non-negative")
    print(f"[CONFIG] {Path(args.config).resolve()}", flush=True)
    try:
        monitor(load_config(args.config).get("gpio", {}), args.duration)
    except KeyboardInterrupt:
        print("Input monitor stopped.")
    except Exception as error:
        raise SystemExit(f"Input monitor failed: {error}. If the GPIO is busy, use the running "
                         "dashboard's [PEOPLE] logs; stop its service only when appropriate.")


if __name__ == "__main__":
    main()
