from __future__ import annotations

import base64
import binascii
import json
import threading
import time

import paho.mqtt.client as mqtt


class MQTTManager(threading.Thread):
    def __init__(self, config, devices, state, modbus, gpio, stop_event):
        super().__init__(name="mqtt-publisher", daemon=True)
        self.cfg, self.devices, self.state = config, devices, state
        self.modbus, self.gpio, self.stop_event = modbus, gpio, stop_event
        self.base = config["base_topic"].rstrip("/")
        self.weather_topic = config.get("weather_topic", f"{self.base}/weather")
        self.people_topic = config.get("people_topic", f"{self.base}/people_count")
        self._connected = threading.Event()
        self._last_disconnect_reason = None
        try:
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                      client_id=config.get("client_id", "machine-rpi"))
        except (AttributeError, TypeError):
            self.client = mqtt.Client(client_id=config.get("client_id", "machine-rpi"))
        if config.get("username"):
            self.client.username_pw_set(config["username"], config.get("password", ""))
        if config.get("tls"):
            self.client.tls_set()
        self.client.will_set(f"{self.base}/status", "offline", qos=1, retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        # Paho retente automatiquement tant que sa boucle réseau tourne.
        # Le délai augmente progressivement afin de ne pas saturer le réseau
        # lorsque le PC hébergeant Mosquitto est arrêté.
        if hasattr(self.client, "reconnect_delay_set"):
            self.client.reconnect_delay_set(
                min_delay=max(1, int(config.get("reconnect_min_s", 2))),
                max_delay=max(2, int(config.get("reconnect_max_s", 30))),
            )

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        is_failure = getattr(reason_code, "is_failure", None)
        failed = bool(is_failure) if is_failure is not None else int(reason_code) != 0
        if failed:
            self._connected.clear()
            self.state.set_mqtt_connected(False)
            print(f"[MQTT] connexion refusée: {reason_code}", flush=True)
            return

        first_connection = not self._connected.is_set()
        self._connected.set()
        self.state.set_mqtt_connected(True)
        if first_connection:
            print(
                f"[MQTT] ONLINE {self.cfg['host']}:{int(self.cfg.get('port', 1883))}",
                flush=True,
            )
        client.publish(f"{self.base}/status", "online", qos=1, retain=True)
        client.subscribe(f"{self.base}/+/parameter/set", qos=1)
        client.subscribe(f"{self.base}/cip/+/set", qos=1)
        client.subscribe(f"{self.base}/cmd/#", qos=1)
        client.subscribe(f"{self.base}/scada/command", qos=1)
        client.subscribe(self.weather_topic, qos=0)
        client.subscribe(self.people_topic, qos=0)

    def _on_disconnect(self, client, userdata, *args):
        self._connected.clear()
        self.state.set_mqtt_connected(False)
        reason = args[-2] if len(args) >= 2 else (args[-1] if args else "inconnue")
        if reason != self._last_disconnect_reason:
            print(
                f"[MQTT] OFFLINE ({reason}) — reconnexion automatique en cours",
                flush=True,
            )
            self._last_disconnect_reason = reason

    def _reject(self, device, payload):
        self.publish_ack({"device": device, "status": "rejected", **payload})

    def _on_message(self, client, userdata, message):
        if message.topic == self.weather_topic:
            return self._on_weather(message.payload)
        if message.topic == self.people_topic:
            return self._on_people(message.payload)
        if message.retain:
            return
        relative = message.topic[len(self.base):].strip("/").split("/")
        if relative == ["scada", "command"]:
            return self._on_scada_json(message.payload)
        if relative and relative[0] == "cmd":
            return self._on_scada_command(relative[1:], message.payload)
        parts = message.topic.split("/")
        if len(parts) >= 3 and parts[-3] == "cip" and parts[-1] == "set":
            return self._on_cip_message(parts[-2], message)
        device_name = parts[-3] if len(parts) >= 3 else ""
        try:
            data = json.loads(message.payload.decode("utf-8"))
            parameter, value = data["parameter"], data["value"]
            if device_name not in self.devices:
                return self._reject(device_name, {"reason": "unknown_device"})
            device = self.devices[device_name]
            if self.gpio and parameter.startswith("cip_"):
                return self._reject(device_name, {
                    "parameter": parameter,
                    "reason": "waveshare_cip_locked_use_cp_io22",
                })
            if parameter in device.get("holding_registers", {}):
                spec = device["holding_registers"][parameter]
                value = int(value)
                if not int(spec["min"]) <= value <= int(spec["max"]):
                    return self._reject(device_name, {
                        "request_id": data.get("request_id"), "parameter": parameter,
                        "reason": "value_out_of_range", "minimum": spec["min"], "maximum": spec["max"],
                    })
            elif parameter in device.get("coils", {}):
                if not isinstance(value, bool) and value not in (0, 1):
                    return self._reject(device_name, {"reason": "boolean_required", "parameter": parameter})
                value = bool(value)
            else:
                return self._reject(device_name, {"reason": "unknown_parameter", "parameter": parameter})
            request_id = self.modbus.enqueue_write({
                "device": device_name, "parameter": parameter, "value": value,
                "request_id": data.get("request_id"),
            })
            self.publish_ack({"request_id": request_id, "device": device_name,
                              "parameter": parameter, "status": "queued"})
        except Exception as exc:
            self._reject(device_name, {"reason": str(exc)})

    def _on_weather(self, payload):
        try:
            text = payload.decode("utf-8").strip()
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = {"temperature_c": float(text), "condition": "unknown"}
            if not isinstance(data, dict):
                raise ValueError("objet JSON attendu")
            temperature = data.get("temperature_c", data.get("temp_c", data.get("temp")))
            temperature = None if temperature is None else round(float(temperature), 1)
            condition = data.get("condition", data.get("weather", "unknown"))
            self.state.update_weather(temperature, condition)
        except Exception as error:
            print(f"[MQTT] météo invalide: {error}", flush=True)

    def _on_people(self, payload):
        try:
            text = payload.decode("utf-8").strip()
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = text
            if isinstance(data, dict):
                data = data.get("people_count", data.get("workers"))
            self.state.update_people_count(data)
        except Exception as error:
            print(f"[MQTT] nombre de workers invalide: {error}", flush=True)

    @staticmethod
    def _scalar(payload):
        text = payload.decode("utf-8").strip()
        lowered = text.lower()
        if lowered in ("true", "on", "enable", "enabled"):
            return 1
        if lowered in ("false", "off", "disable", "disabled"):
            return 0
        return int(float(text))

    def _on_scada_command(self, parts, payload):
        """Commandes scalaires adaptées au Publisher MQTT d'Ecava IGX."""
        target = "unknown"
        try:
            value = self._scalar(payload)
            if len(parts) == 3 and parts[0] == "cip":
                _, name, parameter = parts
                target = f"cip/{name}"
                if self.gpio is None:
                    raise RuntimeError("GPIO CP-IO22 désactivé")
                accepted = self.gpio.enqueue_cip(name, {parameter: value})
                self.client.publish(
                    f"{self.base}/ack/cip/{name}/{parameter}",
                    json.dumps({"status": "accepted", **accepted}, separators=(",", ":")),
                    qos=1, retain=False,
                )
                return

            if len(parts) == 2:
                device_name, parameter = parts
                target = device_name
                if device_name not in self.devices:
                    raise ValueError("appareil inconnu")
                device = self.devices[device_name]
                if self.gpio and parameter.startswith("cip_"):
                    raise ValueError("CIP Waveshare verrouillé OFF")
                if parameter in device.get("holding_registers", {}):
                    spec = device["holding_registers"][parameter]
                    value = int(value)
                    if not int(spec["min"]) <= value <= int(spec["max"]):
                        raise ValueError(f"valeur autorisée: {spec['min']} à {spec['max']}")
                elif parameter in device.get("coils", {}):
                    if value not in (0, 1):
                        raise ValueError("valeur booléenne requise: 0 ou 1")
                    value = bool(value)
                else:
                    raise ValueError("paramètre inconnu")
                request_id = self.modbus.enqueue_write({
                    "device": device_name, "parameter": parameter, "value": value
                })
                self.publish_ack({
                    "request_id": request_id, "device": device_name,
                    "parameter": parameter, "status": "queued",
                })
                return
            raise ValueError("format de topic invalide")
        except Exception as error:
            self.client.publish(
                f"{self.base}/ack/{target}",
                json.dumps({"status": "rejected", "reason": str(error)}, separators=(",", ":")),
                qos=1, retain=False,
            )

    def _on_scada_json(self, payload):
        """Commande JSON brute ou base64(JSON) publiée par Ecava."""
        ack = {"status": "rejected"}
        try:
            data = self.decode_tag_json(payload)
            # Accepte aussi un Publisher qui enveloppe la valeur du tag :
            # {"machine_command_json":"eyJ0YXJnZXQiOi..."}
            if isinstance(data, dict) and "machine_command_json" in data:
                data = self.decode_tag_json(data["machine_command_json"])
            target = data["target"]
            device_name = data["device"]
            parameters = data["parameters"]
            if not isinstance(parameters, dict) or not parameters:
                raise ValueError("parameters doit être un objet JSON non vide")

            accepted = {}
            request_ids = []
            if target == "cip":
                if self.gpio is None:
                    raise RuntimeError("GPIO CP-IO22 désactivé")
                accepted = self.gpio.enqueue_cip(device_name, parameters)
            elif target == "modbus":
                if device_name not in self.devices:
                    raise ValueError("appareil inconnu")
                device = self.devices[device_name]
                for parameter, value in parameters.items():
                    if self.gpio and parameter.startswith("cip_"):
                        raise ValueError("CIP Waveshare verrouillé OFF")
                    if parameter in device.get("holding_registers", {}):
                        spec = device["holding_registers"][parameter]
                        value = int(value)
                        if not int(spec["min"]) <= value <= int(spec["max"]):
                            raise ValueError(
                                f"{parameter}: valeur autorisée {spec['min']} à {spec['max']}"
                            )
                    elif parameter in device.get("coils", {}):
                        if value not in (0, 1, False, True):
                            raise ValueError(f"{parameter}: valeur booléenne requise")
                        value = bool(value)
                    else:
                        raise ValueError(f"paramètre inconnu: {parameter}")
                    request_ids.append(self.modbus.enqueue_write({
                        "device": device_name, "parameter": parameter, "value": value
                    }))
                    accepted[parameter] = value
            elif target == "system":
                if device_name not in ("rpi", "system"):
                    raise ValueError("device system doit être rpi")
                unknown = set(parameters) - {"breaks", "reset_data"}
                if unknown:
                    raise ValueError(f"paramètre système inconnu: {sorted(unknown)[0]}")
                if "breaks" in parameters:
                    breaks = parameters["breaks"]
                    if not isinstance(breaks, list) or len(breaks) != 4:
                        raise ValueError("breaks doit contenir exactement 4 horaires")
                    normalized = []
                    for value in breaks:
                        parsed = time.strptime(str(value), "%H:%M")
                        normalized.append(time.strftime("%H:%M", parsed))
                    normalized.sort()
                    self.state.update_breaks(normalized)
                    accepted["breaks"] = normalized
                if "reset_data" in parameters:
                    if parameters["reset_data"] not in (1, True):
                        raise ValueError("reset_data doit valoir true ou 1")
                    self.state.reset_data()
                    accepted["reset_data"] = True
            else:
                raise ValueError("target doit être cip, modbus ou system")

            ack = {
                "status": "accepted", "target": target, "device": device_name,
                "parameters": accepted, "request_ids": request_ids,
                "timestamp": data.get("timestamp"),
            }
        except Exception as error:
            ack["reason"] = str(error)
        self.client.publish(
            f"{self.base}/scada/ack",
            json.dumps(ack, separators=(",", ":")), qos=1, retain=False,
        )

    @staticmethod
    def decode_tag_json(raw_value):
        """Décode JSON brut ou base64(JSON) provenant d'un tag IntegraXor."""
        if isinstance(raw_value, dict):
            return raw_value
        if isinstance(raw_value, bytes):
            raw_value = raw_value.decode("utf-8")
        if not isinstance(raw_value, str):
            raise ValueError("commande MQTT: texte ou objet JSON attendu")

        text = raw_value.strip()
        if not text:
            raise ValueError("commande MQTT vide")

        # Garde la compatibilité avec MQTT Explorer et les autres clients qui
        # envoient directement un objet JSON sans encodage Base64.
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        try:
            decoded = base64.b64decode(text, validate=True).decode("utf-8")
            return json.loads(decoded)
        except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("commande invalide: JSON ou base64(JSON) attendu") from error

    def _on_cip_message(self, name, message):
        topic = f"{self.base}/cip/{name}/ack"
        try:
            if self.gpio is None:
                raise RuntimeError("GPIO CP-IO22 désactivé")
            data = json.loads(message.payload.decode("utf-8"))
            accepted = self.gpio.enqueue_cip(name, data)
            payload = {"cip": name, "status": "accepted", **accepted}
        except Exception as error:
            payload = {"cip": name, "status": "rejected", "reason": str(error)}
        self.client.publish(topic, json.dumps(payload, separators=(",", ":")), qos=1, retain=False)

    def publish_ack(self, payload):
        device = payload.get("device", "unknown")
        self.client.publish(f"{self.base}/{device}/parameter/ack",
                            json.dumps(payload, separators=(",", ":")), qos=1, retain=False)
        if payload.get("status") == "accepted" and "parameter" in payload:
            self.client.publish(
                f"{self.base}/{device}/parameter/{payload['parameter']}",
                str(int(payload.get("readback_value", payload.get("requested_value", 0)))),
                qos=1, retain=True,
            )

    def _publish_scalar_state(self, snapshot):
        self.client.publish(
            f"{self.base}/rpi/mqtt_connected",
            "1" if snapshot["rpi"].get("mqtt_connected") else "0",
            qos=0, retain=True,
        )
        gpio_error = snapshot["rpi"].get("gpio_error", "")
        self.client.publish(f"{self.base}/rpi/gpio_error", gpio_error, qos=0, retain=True)
        for name, cip in snapshot["rpi"].get("cip", {}).items():
            for key, value in cip.items():
                if isinstance(value, bool):
                    value = int(value)
                self.client.publish(
                    f"{self.base}/cip/{name}/{key}", str(value), qos=0, retain=True
                )
        for name, device in snapshot["devices"].items():
            self.client.publish(
                f"{self.base}/{name}/connected",
                "1" if device.get("connected") else "0", qos=0, retain=True,
            )
            self.client.publish(
                f"{self.base}/{name}/error", device.get("error", ""), qos=0, retain=True,
            )
            for key, value in device.get("values", {}).items():
                if isinstance(value, bool):
                    value = int(value)
                self.client.publish(
                    f"{self.base}/{name}/{key}", str(value), qos=0, retain=True
                )
            for key, value in device.get("parameters", {}).items():
                if isinstance(value, bool):
                    value = int(value)
                self.client.publish(
                    f"{self.base}/{name}/parameter/{key}", str(value), qos=0, retain=True
                )
            self.client.publish(
                f"{self.base}/{name}/parameter_error",
                device.get("parameter_error", ""), qos=0, retain=True,
            )

    def run(self):
        host = self.cfg["host"]
        port = int(self.cfg.get("port", 1883))
        keepalive = int(self.cfg.get("keepalive_s", 30))
        print(
            f"[MQTT] broker {host}:{port} — reconnexion automatique activée",
            flush=True,
        )
        self.client.connect_async(host, port, keepalive)
        self.client.loop_start()
        publish_s = float(self.cfg.get("publish_s", 2.0))
        try:
            while not self.stop_event.wait(publish_s):
                # Ne pas remplir la file interne Paho pendant une longue
                # coupure. Un état complet retained sera envoyé dès le retour.
                if not self._connected.is_set():
                    continue
                snapshot = self.state.snapshot()
                if self.cfg.get("publish_scalar_topics", False):
                    self._publish_scalar_state(snapshot)
                self.client.publish(
                    f"{self.base}/scada/state",
                    json.dumps(snapshot, separators=(",", ":")), qos=1, retain=True,
                )
                if self.cfg.get("publish_legacy_json", False):
                    self.client.publish(f"{self.base}/state",
                                        json.dumps(snapshot, separators=(",", ":")), qos=0, retain=True)
                    self.client.publish(f"{self.base}/rpi/state",
                                        json.dumps(snapshot["rpi"], separators=(",", ":")), qos=0, retain=True)
                    for name, data in snapshot["devices"].items():
                        self.client.publish(f"{self.base}/{name}/state",
                                            json.dumps(data, separators=(",", ":")), qos=0, retain=True)
                    for name, data in snapshot["rpi"].get("cip", {}).items():
                        self.client.publish(f"{self.base}/cip/{name}/state",
                                            json.dumps(data, separators=(",", ":")), qos=0, retain=True)
        finally:
            if self._connected.is_set():
                self.client.publish(f"{self.base}/status", "offline", qos=1, retain=True)
            self._connected.clear()
            self.state.set_mqtt_connected(False)
            self.client.loop_stop()
            self.client.disconnect()
