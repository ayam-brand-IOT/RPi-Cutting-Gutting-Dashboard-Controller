"""Physical button authorization and LED tests without hardware."""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from config_loader import load_config
from gpio_manager import GPIOManager
from state import StateStore


class ButtonTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config("config.yaml")["gpio"]
        self.manager = GPIOManager(self.cfg, StateStore([]), threading.Event())
        self.manager.inputs = {name: SimpleNamespace(value=False) for name in
                               ("system_run_toggle", "water_run_toggle")}
        self.manager.outputs = {pin: SimpleNamespace(value=False) for pin in range(17, 23)}
        self.now = 0.0
        self.sample()

    def sample(self, name=None, value=False):
        if name:
            self.manager.inputs[name].value = value
        self.manager._publish_state(self.now)
        self.now += .25
        self.manager._publish_state(self.now)
        self.now += .25

    def press(self, name):
        self.sample(name, True)
        self.sample(name, False)

    def test_startup_and_remote_commands_cannot_open_valves(self):
        self.manager._run_cycles(100)
        for name in self.manager.channels:
            self.manager.enqueue_cip(name, {"enable": True, "on_ms": 3000})
        self.manager._apply_commands(101)
        self.manager._run_cycles(102)
        self.assertTrue(all(not output.value for output in self.manager.outputs.values()))

    def test_water_cycle_led_and_independent_cleaning(self):
        self.press("water_run_toggle")
        self.assertTrue(self.manager.outputs[20].value)
        self.assertTrue(self.manager.outputs[22].value)
        self.assertFalse(self.manager.outputs[17].value)
        self.assertFalse(self.manager.outputs[21].value)
        deadline = self.manager.channels["water_intake"]["deadline"]
        self.manager._run_cycles(deadline)
        self.assertFalse(self.manager.outputs[20].value)
        self.assertTrue(self.manager.outputs[22].value)
        self.manager._run_cycles(deadline + 10)
        self.assertTrue(self.manager.outputs[20].value)
        self.press("system_run_toggle")
        self.assertTrue(all(self.manager.outputs[p].value for p in range(17, 23)))
        self.press("water_run_toggle")
        self.assertFalse(self.manager.outputs[20].value)
        self.assertFalse(self.manager.outputs[22].value)
        self.assertTrue(self.manager.outputs[17].value)
        self.press("system_run_toggle")
        self.assertTrue(all(not output.value for output in self.manager.outputs.values()))

    def test_held_button_bounce_and_restart(self):
        button = "water_run_toggle"
        self.manager.inputs[button].value = True
        self.manager._publish_state(1)
        self.manager.inputs[button].value = False
        self.manager._publish_state(1.05)
        self.manager._publish_state(1.3)
        self.assertFalse(self.manager.outputs[22].value)
        self.now = 2
        self.sample(button, True)
        for _ in range(5):
            self.sample()
        self.assertTrue(self.manager.outputs[22].value)
        restarted = GPIOManager(self.cfg, StateStore([]), threading.Event())
        restarted.inputs = {button: SimpleNamespace(value=True)}
        restarted._publish_state(0)
        restarted._publish_state(1)
        self.assertFalse(restarted._group_enabled["water"])

    def test_setup_pinout_and_active_low_outputs(self):
        with patch("gpio_manager.DigitalInputDevice") as inp, patch("gpio_manager.DigitalOutputDevice") as out:
            self.manager._setup()
        self.assertEqual({c.args[0] for c in inp.call_args_list}, {4, 5, 8, 9, 10, 11})
        self.assertEqual({c.args[0] for c in out.call_args_list}, set(range(17, 23)))
        for call in out.call_args_list:
            self.assertFalse(call.kwargs["active_high"])
            self.assertFalse(call.kwargs["initial_value"])


if __name__ == "__main__":
    unittest.main()
