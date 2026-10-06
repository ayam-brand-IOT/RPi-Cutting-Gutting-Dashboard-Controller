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
    """Pilote les GPIO du CP-IO22 et les cycles CIP sans blocage."""

    def __init__(self, config: dict, state, stop_event: threading.Event):
        super().__init__(name="gpio-cip", daemon=True)
        self.config, self.state, self.stop_event = config, state, stop_event
        self.inputs, self.outputs = {}, {}
        self.commands: queue.Queue[dict] = queue.Queue(maxsize=50)
        self.channels = {}
        self.input_cfg = config.get("inputs", {})
        self._button_samples = {}
        self._button_stable = {}
        self._people_counts = {"left": 0, "right": 0}
        self._cip_master_enabled = True
        self.toggle_groups = config.get("toggle_groups", {})
        # Physical button authorizations are never restored after a restart.
        self._group_enabled = {name: False for name in self.toggle_groups}

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

        self._channel_groups = {}
        output_pins = [channel["pin"] for channel in self.channels.values()]
        for group, item in self.toggle_groups.items():
            for name in item["channels"]:
                if name not in self.channels or name in self._channel_groups:
                    raise ValueError(f"Invalid or duplicate button channel: {name}")
                self._channel_groups[name] = group
            output_pins.append(int(item["led_pin"]))
        if len(output_pins) != len(set(output_pins)):
            raise ValueError("Duplicate output pin")
        if any(pin not in range(17, 28) for pin in output_pins):
            raise ValueError("Not a CP-IO22 output pin")
        for item in self.input_cfg.values():
            if "toggle_group" in item and item["toggle_group"] not in self.toggle_groups:
                raise ValueError("Unknown button toggle group")

    def _channel_allowed(self, name):
        group = self._channel_groups.get(name)
        return (self._cip_master_enabled and self.channels[name]["enable"]
                and (group is None or self._group_enabled[group]))

    def _toggle_group(self, group, now):
        self._group_enabled[group] = not self._group_enabled[group]
        for name in self.toggle_groups[group]["channels"]:
            channel = self.channels[name]
            active = self._channel_allowed(name)
            self._write(channel, active)
            channel.update(phase="on" if active else "disabled",
                           deadline=now + channel["on_ms"] / 1000.0 if active else 0.0)
        self._sync_leds()

    def _sync_leds(self):
        for group, item in self.toggle_groups.items():
            pin = int(item["led_pin"])
            if pin in self.outputs:
                self.outputs[pin].value = self._group_enabled[group] and self._cip_master_enabled

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
        if any(cfg.get("people_side") in ("left", "right") for cfg in self.input_cfg.values()):
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

        for item in self.toggle_groups.values():
            self.outputs[int(item["led_pin"])] = DigitalOutputDevice(
                int(item["led_pin"]),
                active_high=bool(item.get("led_active_high", True)),
                initial_value=False,
            )

    def _toggle_cip_master(self):
        self._cip_master_enabled = not self._cip_master_enabled
        if not self._cip_master_enabled:
            for channel in self.channels.values():
                if channel["output"]:
                    self._write(channel, False)
                channel.update(phase="disabled", deadline=0.0)
            return
        for name, channel in self.channels.items():
            if self._channel_allowed(name) and channel["phase"] == "disabled":
                channel.update(phase="on", deadline=time.monotonic() + channel["on_ms"] / 1000.0)
                self._write(channel, True)

    def _apply_commands(self, now: float):
        while True:
            try:
                command = self.commands.get_nowait()
            except queue.Empty:
                return
            channel = self.channels[command["name"]]
            channel.update(command["values"])
            if not self._channel_allowed(command["name"]):
                self._write(channel, False)
                channel.update(phase="disabled", deadline=0.0)
            else:
                # L'activation démarre immédiatement par la phase ON.
                self._write(channel, True)
                channel.update(phase="on", deadline=now + channel["on_ms"] / 1000.0)

    def _run_cycles(self, now: float):
        if not self._cip_master_enabled:
            for channel in self.channels.values():
                if channel["output"]:
                    self._write(channel, False)
                channel.update(phase="disabled", deadline=0.0)
            return
        for name, channel in self.channels.items():
            if not self._channel_allowed(name):
                if channel["output"]:
                    self._write(channel, False)
                channel.update(phase="disabled", deadline=0.0)
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
        presses = {"left": [], "right": []}
        for name, device in self.inputs.items():
            value = bool(device.value)
            cfg = self.input_cfg[name]
            if cfg.get("cip_toggle") or cfg.get("toggle_group"):
                previous = self._button_samples.get(name)
                if previous is None or previous[0] != value:
                    self._button_samples[name] = (value, now)
                if now - self._button_samples[name][1] >= float(cfg.get("debounce_s", 0.1)):
                    stable = self._button_stable.get(name)
                    self._button_stable[name] = value
                    if stable is False and value:
                        if cfg.get("toggle_group"):
                            self._toggle_group(cfg["toggle_group"], now)
                        else:
                            self._toggle_cip_master()
                if name in self._button_stable:
                    values[name] = self._button_stable[name]
            elif cfg.get("people_side") in ("left", "right"):
                previous = self._button_samples.get(name)
                if previous is None or previous[0] != value:
                    self._button_samples[name] = (value, now)
                if now - self._button_samples[name][1] >= float(cfg.get("debounce_s", 0.1)):
                    stable = self._button_stable.get(name)
                    self._button_stable[name] = value
                    # A held button at startup is not a press. First release it.
                    if stable is False and value:
                        presses[cfg["people_side"]].append(int(cfg["people_delta"]))
                if name in self._button_stable:
                    values[name] = self._button_stable[name]
            else:
                values[name] = value
        people = None
        if any(cfg.get("people_side") in ("left", "right") for cfg in self.input_cfg.values()):
            people = {}
            for side in ("left", "right"):
                names = [name for name, cfg in self.input_cfg.items()
                         if cfg.get("people_side") == side and cfg.get("enabled", True)]
                ready = (len(names) == 2 and all(name in values for name in names)
                         and {self.input_cfg[name].get("people_delta") for name in names} == {-1, 1})
                if ready:
                    # Opposite presses in the same sample cancel, even at zero.
                    self._people_counts[side] = max(0, self._people_counts[side] + sum(presses[side]))
                people[side] = self._people_counts[side] if ready else None
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
        self._sync_leds()
        for group in self.toggle_groups:
            values[group + "_enabled"] = self._group_enabled[group] and self._cip_master_enabled
        self.state.update_gpio(values, cip, people_counts=people)

    def run(self):
        try:
            self._setup()
            pins = "/".join(str(channel["pin"]) for channel in self.channels.values())
            print(f"[GPIO] CP-IO22 ONLINE via gpiozero — CIP sur GPIO{pins}", flush=True)
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
            for output in self.outputs.values():
                output.value = False
            for device in (*self.inputs.values(), *self.outputs.values()):
                device.close()
