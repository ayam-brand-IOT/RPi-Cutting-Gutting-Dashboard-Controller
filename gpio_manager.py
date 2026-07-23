from __future__ import annotations

import queue
import threading
import time

try:
    from gpiozero import DigitalInputDevice, DigitalOutputDevice
except ImportError:
    DigitalInputDevice = DigitalOutputDevice = None


class GPIOManager(threading.Thread):
    """Pilote les GPIO du CP-IO22 et les trois cycles CIP sans blocage."""

    def __init__(self, config: dict, state, stop_event: threading.Event):
        super().__init__(name="gpio-cip", daemon=True)
        self.config, self.state, self.stop_event = config, state, stop_event
        self.inputs, self.outputs = {}, {}
        self.commands: queue.Queue[dict] = queue.Queue(maxsize=50)
        self.channels = {}
        self.input_cfg = config.get("inputs", {})

        for name, item in config.get("cip", {}).items():
            self.channels[name] = {
                "pin": int(item["pin"]),
                "active_high": bool(item.get("active_high", True)),
                "enable": bool(item.get("enable", False)),
                "on_ms": int(item.get("on_ms", 200)),
                "off_ms": int(item.get("off_ms", 8000)),
                "output": False,
                "phase": "disabled",
                "deadline": 0.0,
            }

    def enqueue_cip(self, name: str, values: dict):
        if name not in self.channels:
            raise KeyError("CIP inconnu")
        allowed = {"enable", "on_ms", "off_ms"}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"paramètre inconnu: {sorted(unknown)[0]}")

        command = {}
        if "enable" in values:
            value = values["enable"]
            if not isinstance(value, bool) and value not in (0, 1):
                raise ValueError("enable doit être true/false ou 0/1")
            command["enable"] = bool(value)
        for key in ("on_ms", "off_ms"):
            if key in values:
                value = int(values[key])
                if not 100 <= value <= 60000:
                    raise ValueError(f"{key} doit être entre 100 et 60000 ms")
                command[key] = value
        if not command:
            raise ValueError("aucun paramètre fourni")
        self.commands.put_nowait({"name": name, "values": command})
        return command

    def _write(self, channel: dict, active: bool):
        self.outputs[channel["pin"]].value = bool(active)
        channel["output"] = active

    def _setup(self):
        if DigitalInputDevice is None:
            raise RuntimeError("gpiozero n'est pas installé")

        for name, item in self.input_cfg.items():
            if not item.get("enabled", True):
                continue
            try:
                pull_up = item.get("pull_up", True)
                kwargs = {"pull_up": pull_up}
                if pull_up is None:
                    kwargs["active_state"] = not item.get("active_low", False)
                self.inputs[name] = DigitalInputDevice(int(item["pin"]), **kwargs)
            except Exception as error:
                # Une entrée de monitoring ne doit jamais empêcher les sorties
                # CIP de sécurité de démarrer.
                print(f"[GPIO] entrée {name} ignorée: {error}", flush=True)

        # Sécurité : toutes les électrovannes sont OFF avant de démarrer les cycles.
        for channel in self.channels.values():
            pin = channel["pin"]
            self.outputs[pin] = DigitalOutputDevice(
                pin,
                active_high=channel["active_high"],
                initial_value=False,
            )
            self._write(channel, False)

    def _apply_commands(self, now: float):
        while True:
            try:
                command = self.commands.get_nowait()
            except queue.Empty:
                return
            channel = self.channels[command["name"]]
            channel.update(command["values"])
            if not channel["enable"]:
                self._write(channel, False)
                channel.update(phase="disabled", deadline=0.0)
            else:
                # L'activation démarre immédiatement par la phase ON.
                self._write(channel, True)
                channel.update(phase="on", deadline=now + channel["on_ms"] / 1000.0)

    def _run_cycles(self, now: float):
        for channel in self.channels.values():
            if not channel["enable"]:
                continue
            if channel["phase"] == "disabled":
                self._write(channel, True)
                channel.update(phase="on", deadline=now + channel["on_ms"] / 1000.0)
            elif now >= channel["deadline"]:
                if channel["phase"] == "on":
                    self._write(channel, False)
                    channel.update(phase="off", deadline=now + channel["off_ms"] / 1000.0)
                else:
                    self._write(channel, True)
                    channel.update(phase="on", deadline=now + channel["on_ms"] / 1000.0)

    def _publish_state(self):
        values = {}
        for name, device in self.inputs.items():
            values[name] = bool(device.value)
        cip = {
            name: {
                "enable": channel["enable"],
                "on_ms": channel["on_ms"],
                "off_ms": channel["off_ms"],
                "output": channel["output"],
                "phase": channel["phase"],
                "pin": channel["pin"],
            }
            for name, channel in self.channels.items()
        }
        self.state.update_gpio(values, cip)

    def run(self):
        try:
            self._setup()
            print("[GPIO] CP-IO22 ONLINE via gpiozero — CIP sur GPIO17/18/19", flush=True)
            while not self.stop_event.is_set():
                now = time.monotonic()
                self._apply_commands(now)
                self._run_cycles(now)
                self._publish_state()
                self.stop_event.wait(0.02)
        except Exception as error:
            print(f"[GPIO] ERREUR: {error}", flush=True)
            self.state.set_gpio_error(str(error))
        finally:
            for channel in self.channels.values():
                pin = channel["pin"]
                if pin in self.outputs:
                    self._write(channel, False)
            for device in (*self.inputs.values(), *self.outputs.values()):
                device.close()
