import threading

from gpio_manager import GPIOManager
from state import StateStore


class FakeOutput:
    def __init__(self):
        self.value = False


class FakeInput:
    def __init__(self, value):
        self.value = value

    def close(self):
        pass


def test_button_starts_and_stops_cip():
    cfg = {
        "inputs": {
            "system_toggle": {
                "enabled": True,
                "pin": 6,
                "pull_up": None,
                "active_low": False,
                "cip_toggle": True,
                "debounce_s": 0.0,
            }
        },
        "cip": {
            "gutting_left": {"pin": 17, "active_high": True, "enable": True, "on_s": 2, "off_s": 10}
        },
    }
    manager = GPIOManager(cfg, StateStore([]), threading.Event())
    manager.inputs = {"system_toggle": FakeInput(False)}
    manager.outputs = {17: FakeOutput()}
    manager.channels["gutting_left"] = {
        "pin": 17,
        "active_high": True,
        "enable": True,
        "on_ms": 2000,
        "off_ms": 10000,
        "output": False,
        "phase": "disabled",
        "deadline": 0.0,
    }
    manager._button_samples = {"system_toggle": (False, 0.0)}
    manager._button_stable = {"system_toggle": False}
    manager._publish_state(now=1.0)
    assert manager._cip_master_enabled is True

    manager.inputs["system_toggle"].value = True
    manager._publish_state(now=2.0)
    assert manager._cip_master_enabled is False

    manager.inputs["system_toggle"].value = False
    manager._publish_state(now=3.0)
    assert manager._cip_master_enabled is False

    manager.inputs["system_toggle"].value = True
    manager._publish_state(now=4.0)
    assert manager._cip_master_enabled is True


def main():
    cfg = {"cip": {
        "gutting_left": {"pin": 17, "on_ms": 200, "off_ms": 8000},
        "cutting": {"pin": 18, "on_ms": 200, "off_ms": 8000},
        "gutting_right": {"pin": 19, "on_ms": 200, "off_ms": 8000},
    }}
    manager = GPIOManager(cfg, StateStore([]), threading.Event())
    manager.outputs = {17: FakeOutput(), 18: FakeOutput(), 19: FakeOutput()}
    manager.enqueue_cip("gutting_left", {"enable": True, "on_ms": 300})
    manager._apply_commands(10.0)
    assert manager.outputs[17].value is True
    assert manager.channels["gutting_left"]["deadline"] == 10.3
    manager._run_cycles(10.3)
    assert manager.outputs[17].value is False
    manager.enqueue_cip("gutting_left", {"enable": False})
    manager._apply_commands(11.0)
    assert manager.outputs[17].value is False
    print("GPIO CIP OK — activation, cycle et arrêt validés")


if __name__ == "__main__":
    main()
