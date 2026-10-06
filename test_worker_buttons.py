"""Worker button mapping and published counters, without a display dependency."""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from config_loader import load_config
from gpio_manager import GPIOManager
from state import StateStore


class WorkerButtonTests(unittest.TestCase):
    def test_each_minus_pin_decrements_its_side_and_logs_the_change(self):
        cfg = load_config("config.yaml")["gpio"]
        cfg["debug_buttons"] = True
        manager = GPIOManager(cfg, StateStore([]), threading.Event())
        names = {item["pin"]: name for name, item in cfg["inputs"].items()
                 if item.get("people_side")}
        manager.inputs = {name: SimpleNamespace(value=False) for name in names.values()}
        now = 0.0

        def sample():
            nonlocal now
            manager._publish_state(now)
            now += .25
            manager._publish_state(now)
            now += .25

        def press(pin):
            manager.inputs[names[pin]].value = True
            sample()
            manager.inputs[names[pin]].value = False
            sample()

        def counts():
            return manager.state.snapshot()["rpi"]["people_gpio"]

        with patch("builtins.print") as output:
            sample()
            press(4)
            press(4)
            press(8)
            self.assertEqual(counts(), {"left": 2, "right": 1})
            press(5)
            self.assertEqual(counts(), {"left": 1, "right": 1})
            press(9)
            self.assertEqual(counts(), {"left": 1, "right": 0})
            press(9)
            self.assertEqual(counts(), {"left": 1, "right": 0})
        messages = [call.args[0] for call in output.call_args_list]
        self.assertTrue(any("worker pin=5 side=left accepted_delta=-1" in m for m in messages))
        self.assertTrue(any("workers side=left count=2->1" in m for m in messages))


if __name__ == "__main__":
    unittest.main()
