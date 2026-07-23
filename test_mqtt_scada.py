import threading
import sys
import types
import base64
import json

try:
    import paho.mqtt.client  # noqa: F401
except ImportError:
    client_module = types.ModuleType("paho.mqtt.client")

    class CallbackAPIVersion:
        VERSION2 = 2

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

        def will_set(self, *args, **kwargs):
            pass

    client_module.CallbackAPIVersion = CallbackAPIVersion
    client_module.Client = DummyClient
    mqtt_module = types.ModuleType("paho.mqtt")
    mqtt_module.client = client_module
    paho_module = types.ModuleType("paho")
    paho_module.mqtt = mqtt_module
    sys.modules.update({
        "paho": paho_module, "paho.mqtt": mqtt_module,
        "paho.mqtt.client": client_module,
    })

from mqtt_manager import MQTTManager
from state import StateStore


class FakeClient:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, str(payload), qos, retain))


class FakeGPIO:
    def __init__(self):
        self.command = None

    def enqueue_cip(self, name, values):
        self.command = (name, values)
        return values


class FakeModbus:
    def __init__(self):
        self.command = None

    def enqueue_write(self, command):
        self.command = command
        return "request-test"


def main():
    devices = {"gutting_left": {
        "holding_registers": {
            "eject_enable": {"address": 2, "min": 0, "max": 1},
            "cip_enable": {"address": 12, "min": 0, "max": 1},
        }, "coils": {},
    }}
    gpio, modbus = FakeGPIO(), FakeModbus()
    manager = MQTTManager(
        {"base_topic": "factory/cutting-gutting"}, devices,
        StateStore(list(devices)), modbus, gpio, threading.Event(),
    )
    manager.client = FakeClient()
    manager._on_scada_command(["cip", "gutting_left", "on_ms"], b"500")
    assert gpio.command == ("gutting_left", {"on_ms": 500})
    manager._on_scada_command(["gutting_left", "eject_enable"], b"1")
    assert modbus.command["value"] == 1
    manager._on_scada_command(["gutting_left", "cip_enable"], b"1")
    assert any("rejected" in payload for _, payload, _, _ in manager.client.published)
    manager._on_scada_json(json.dumps({
        "target": "cip", "device": "cutting",
        "parameters": {"enable": 1, "on_ms": 700}, "timestamp": 123,
    }).encode())
    assert gpio.command == ("cutting", {"enable": 1, "on_ms": 700})
    manager._on_scada_json(json.dumps({
        "machine_command_json": json.dumps({
            "target": "modbus", "device": "gutting_left",
            "parameters": {"eject_enable": 0},
        })
    }).encode())
    assert modbus.command["parameter"] == "eject_enable"
    assert modbus.command["value"] == 0

    ecava_command = {
        "target": "modbus", "device": "gutting_left",
        "parameters": {"eject_enable": 1}, "timestamp": 456,
    }
    ecava_payload = base64.b64encode(
        json.dumps(ecava_command, separators=(",", ":")).encode("utf-8")
    )
    manager._on_scada_json(ecava_payload)
    assert modbus.command["parameter"] == "eject_enable"
    assert modbus.command["value"] == 1

    snapshot = manager.state.snapshot()
    snapshot["rpi"]["cip"] = {"gutting_left": {
        "enable": True, "on_ms": 500, "off_ms": 8000, "output": False,
    }}
    snapshot["devices"]["gutting_left"].update({
        "connected": True,
        "values": {"rpm_blade": 2450},
        "parameters": {"eject_enable": 1},
    })
    manager._publish_scalar_state(snapshot)
    published = {(topic, payload) for topic, payload, _, _ in manager.client.published}
    assert ("factory/cutting-gutting/gutting_left/rpm_blade", "2450") in published
    assert ("factory/cutting-gutting/cip/gutting_left/enable", "1") in published
    assert ("factory/cutting-gutting/gutting_left/parameter/eject_enable", "1") in published
    print("MQTT SCADA OK — commandes scalaires et télémétrie validées")


if __name__ == "__main__":
    main()
