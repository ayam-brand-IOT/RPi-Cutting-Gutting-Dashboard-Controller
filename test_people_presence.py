"""Debounced people-count button tests without hardware or MQTT."""
import os
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import yaml
import gpio_manager
from gpio_manager import GPIOManager
from state import StateStore
from dashboard_cycle import _machine_people
from dashboard import _people_count


class PresenceTests(unittest.TestCase):
    def setUp(self):
        with open("config.yaml", encoding="utf-8") as stream:
            self.cfg = yaml.safe_load(stream)["gpio"]
        self.state = StateStore([])
        self.manager = GPIOManager(self.cfg, self.state, threading.Event())
        self.names = [n for n, c in self.cfg["inputs"].items() if c.get("people_side")]
        self.manager.inputs = {n: SimpleNamespace(value=False) for n in self.names}
        self.now = 0.0

    def sample(self, name=None, value=False):
        if name:
            self.manager.inputs[name].value = value
        self.manager._publish_state(self.now)
        self.now += .2
        self.manager._publish_state(self.now)
        self.now += .2

    def count(self, side):
        return _machine_people(self.state.snapshot(), side)

    def press(self, name):
        self.sample(name, True)
        self.sample(name, False)

    def test_pinout(self):
        self.assertEqual([self.cfg["inputs"][n]["pin"] for n in self.names], [4, 5, 8, 9])
        self.assertEqual([self.cfg["inputs"][n]["people_delta"] for n in self.names], [1, -1, 1, -1])
        self.assertEqual({c["pin"] for c in self.cfg["cip"].values()}, {17, 18, 19, 20})

    def test_independent_counters_total_and_zero_floor(self):
        self.sample()
        for _ in range(6):
            self.press("people_left_plus")
        self.press("people_right_plus")
        self.press("people_left_minus")
        self.assertEqual(self.count("left"), 5)
        self.assertEqual(self.count("right"), 1)
        self.assertEqual(_people_count(self.state.snapshot(), {}), 6)
        for _ in range(3):
            self.press("people_right_minus")
        self.assertEqual(self.count("right"), 0)
        self.assertEqual(self.count("left"), 5)

    def test_hold_and_release_required(self):
        self.sample()
        self.sample("people_left_plus", True)
        for _ in range(10):
            self.sample()
        self.assertEqual(self.count("left"), 1)
        self.sample("people_left_plus", False)
        self.press("people_left_plus")
        self.assertEqual(self.count("left"), 2)

    def test_bounce_short_press_and_short_release(self):
        self.sample()
        button = self.manager.inputs["people_left_plus"]
        for t, pressed in [(1, True), (1.02, False), (1.04, True), (1.08, False), (1.3, False)]:
            button.value = pressed
            self.manager._publish_state(t)
        self.assertEqual(self.count("left"), 0)
        button.value = True
        self.manager._publish_state(2)
        self.manager._publish_state(2.2)
        self.assertEqual(self.count("left"), 1)
        for t, pressed in [(2.3, False), (2.34, True), (2.6, True)]:
            button.value = pressed
            self.manager._publish_state(t)
        self.assertEqual(self.count("left"), 1)

    def test_startup_held_button_is_ignored(self):
        self.sample("people_left_plus", True)
        self.assertEqual(self.count("left"), 0)
        self.sample("people_left_plus", False)
        self.press("people_left_plus")
        self.assertEqual(self.count("left"), 1)

    def test_simultaneous_opposite_presses_cancel(self):
        self.sample()
        for name in ("people_left_plus", "people_left_minus"):
            self.manager.inputs[name].value = True
        self.sample()
        self.assertEqual(self.count("left"), 0)

    def test_missing_input_fault_and_mqtt_priority(self):
        del self.manager.inputs["people_left_plus"]
        self.sample()
        self.state.update_people_counts(4, 4)
        self.assertIsNone(self.count("left"))
        self.assertEqual(self.count("right"), 0)
        self.assertIsNone(_people_count(self.state.snapshot(), {}))
        self.state.set_gpio_error("test failure")
        self.assertIsNone(self.count("right"))

    def test_setup_uses_external_high_logic(self):
        with patch.object(gpio_manager, "DigitalInputDevice") as inp, patch.object(gpio_manager, "DigitalOutputDevice"):
            self.manager._setup()
            self.assertEqual(inp.call_count, 6)
            for call in inp.call_args_list:
                self.assertIsNone(call.kwargs["pull_up"])
                self.assertTrue(call.kwargs["active_state"])
        self.assertIsNone(_people_count(self.state.snapshot(), {}))

    def test_legacy_gpio_does_not_invalidate_mqtt(self):
        state = StateStore([])
        state.update_people_counts(2, 3)
        state.set_gpio_error("unrelated GPIO failure")
        self.assertEqual(_machine_people(state.snapshot(), "left"), 2)


if __name__ == "__main__":
    unittest.main()
