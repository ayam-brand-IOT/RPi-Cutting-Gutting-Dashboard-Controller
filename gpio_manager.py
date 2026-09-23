from __future__ import annotations

import math

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
        self._presence_samples = {}
        self._presence_stable = {}

        for name, item in config.get("cip", {}).items():
            durations = {}
            for stem, default_ms in (("on", 2000), ("off", 10000)):
                if stem + "_s" in item:
                    if stem + "_ms" in item:
                        raise ValueError(f"CIP {stem}: specify seconds or milliseconds, not both")
                    seconds = float(item[stem + "_s"])
                    if isinstance(item[stem + "_s"], bool) or not math.isfinite(seconds) or not 0.1 <= seconds <= 60:
                        raise ValueError("CIP duration must be 0.1–60 seconds")
                    ms = round(seconds * 1000)
                    if abs(seconds * 1000 - ms) > 0.000001:
                        raise ValueError("CIP seconds support at most 3 decimal places")
                    durations[stem] = ms
                else:
                    durations[stem] = int(item.get(stem + "_ms", default_ms))
            self.channels[name] = {
                "pin": int(item["pin"]),
                "active_high": bool(item.get("active_high", True)),
                "enable": bool(item.get("enable", False)),
                "on_ms": durations["on"],
                "off_ms": durations["off"],
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
        if any(cfg.get("presence_side") in ("left", "right") for cfg in self.input_cfg.values()):
            self.state.update_gpio({}, people_counts={"left": None, "right": None})
        if DigitalInputDevice is None:
            raise RuntimeError("gpiozero n'est pas installé")

        for name, item in self.input_cfg.items():
            if not item.get("enabled", True):
                continue
            try:
                pin = int(item["pin"])
                if pin not in (*range(4, 14), 16):
                    raise ValueError("not a CP-IO22 input pin")
                if any(int(cfg["pin"]) == pin for cfg in self.input_cfg.values()
                       if cfg is not item and cfg.get("enabled", True)):
                    raise ValueError("duplicate input pin")
                if pin in {channel["pin"] for channel in self.channels.values()}:
                    raise ValueError("pin already assigned to an output")
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

    def _publish_state(self, now=None):
        now = time.monotonic() if now is None else now
        values = {}
        for name, device in self.inputs.items():
            value = bool(device.value)
            cfg = self.input_cfg[name]
            if cfg.get("presence_side") in ("left", "right"):
                previous = self._presence_samples.get(name)
                if previous is None or previous[0] != value:
                    self._presence_samples[name] = (value, now)
                if now - self._presence_samples[name][1] >= float(cfg.get("debounce_s", 0.1)):
                    self._presence_stable[name] = value
                if name in self._presence_stable:
                    values[name] = self._presence_stable[name]
            else:
                values[name] = value
        people = None
        if any(cfg.get("presence_side") in ("left", "right") for cfg in self.input_cfg.values()):
            people = {}
            for side in ("left", "right"):
                names = [name for name, cfg in self.input_cfg.items()
                         if cfg.get("presence_side") == side and cfg.get("enabled", True)]
                people[side] = (sum(values[name] for name in names)
                                if len(names) == 4 and all(name in values for name in names)
                                else None)
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
        self.state.update_gpio(values, cip, people_counts=people)

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
