"""Presence input tests, without GPIO hardware or MQTT connections."""
import os
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import itertools
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
        self.names = [n for n, c in self.cfg["inputs"].items() if c.get("presence_side")]
        self.manager.inputs = {n: SimpleNamespace(value=False) for n in self.names}

    def test_pinout(self):
        self.assertEqual([self.cfg["inputs"][n]["pin"] for n in self.names], list(range(4, 12)))
        self.assertEqual([self.cfg["inputs"][n]["presence_side"] for n in self.names], ["left"] * 4 + ["right"] * 4)
        self.assertEqual({c["pin"] for c in self.cfg["cip"].values()}, {17, 18, 19})
        self.assertEqual(self.cfg["inputs"]["cutting_motors_on"]["pin"], 12)
        self.assertEqual(self.cfg["inputs"]["cutting_motors_trip"]["pin"], 13)

    def test_all_256_combinations(self):
        for i, bits in enumerate(itertools.product((False, True), repeat=8)):
            for name, bit in zip(self.names, bits):
                self.manager.inputs[name].value = bit
            self.manager._publish_state(i * 2.0)
            self.manager._publish_state(i * 2.0 + 0.2)
            snap = self.state.snapshot()
            self.assertEqual(_machine_people(snap, "left"), sum(bits[:4]))
            self.assertEqual(_machine_people(snap, "right"), sum(bits[4:]))
            self.assertEqual(_people_count(snap, {}), sum(bits))

    def test_initial_debounce_and_short_pulse(self):
        self.manager._publish_state(0)
        self.assertIsNone(_machine_people(self.state.snapshot(), "left"))
        self.manager._publish_state(.2)
        self.manager.inputs[self.names[0]].value = True
        self.manager._publish_state(.3)
        self.manager.inputs[self.names[0]].value = False
        self.manager._publish_state(.35)
        self.manager._publish_state(.6)
        self.assertEqual(_machine_people(self.state.snapshot(), "left"), 0)

    def test_missing_input_fault_and_mqtt_priority(self):
        del self.manager.inputs[self.names[0]]
        self.manager._publish_state(0)
        self.manager._publish_state(.2)
        self.state.update_people_counts(4, 4)
        self.assertIsNone(_machine_people(self.state.snapshot(), "left"))
        self.assertEqual(_machine_people(self.state.snapshot(), "right"), 0)
        self.assertIsNone(_people_count(self.state.snapshot(), {}))
        self.state.set_gpio_error("test failure")
        self.assertIsNone(_machine_people(self.state.snapshot(), "right"))

    def test_setup_uses_external_high_logic(self):
        with patch.object(gpio_manager, "DigitalInputDevice") as inp, patch.object(gpio_manager, "DigitalOutputDevice"):
            self.manager._setup()
            self.assertEqual(inp.call_count, 8)
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
