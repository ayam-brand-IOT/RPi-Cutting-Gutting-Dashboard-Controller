import threading

from gpio_manager import GPIOManager
from state import StateStore


class FakeOutput:
    def __init__(self):
        self.value = False


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
