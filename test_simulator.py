"""Regression checks for the Tk simulator configuration and datastore."""
import tempfile
import unittest
from pathlib import Path

from config_loader import load_config
from simulator import ModbusSimulator, make_cip_simulator


class SimulatorTests(unittest.TestCase):
    def test_water_intake_cycle_and_state(self):
        cfg = load_config("config.yaml")
        manager = make_cip_simulator(cfg["gpio"])
        self.assertEqual(len(manager.channels), 4)
        self.assertEqual(manager.channels["water_intake"]["pin"], 20)
        manager._run_cycles(10.0)
        self.assertTrue(manager.outputs[20].value)
        manager._run_cycles(12.0)
        self.assertFalse(manager.outputs[20].value)
        manager._run_cycles(22.0)
        self.assertTrue(manager.outputs[20].value)
        manager.enqueue_cip("water_intake", {"enable": False})
        manager._apply_commands(22.1)
        manager._publish_state(22.1)
        self.assertFalse(manager.outputs[20].value)
        self.assertEqual(manager.state.snapshot()["rpi"]["cip"]["water_intake"]["phase"], "disabled")
        self.assertTrue(manager.outputs[17].value)

    def test_gutting_coils_are_not_holding_registers(self):
        cfg = load_config("config.yaml")
        for name in ("gutting_left", "gutting_right"):
            device = cfg["devices"][name]
            self.assertNotIn("coils", device["holding_registers"])
            self.assertNotIn("eject_enable", device["holding_registers"])
            self.assertEqual(device["coils"]["eject_enable"]["address"], 0)
            self.assertEqual(device["coils"]["cip_enable"]["address"], 1)
            self.assertEqual(device["coils"]["alarm_ack"]["address"], 2)

    def test_misindented_empty_register_reports_yaml_path(self):
        bad = "devices:\n  gutting_left:\n    holding_registers:\n      coils:\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text(bad, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, r"devices\.gutting_left\.holding_registers\.coils"):
                load_config(str(path))

    def test_real_datastore_at_fuji_and_schneider_addresses(self):
        cfg = load_config("config.yaml")
        devices = {name: dev for name, dev in cfg["devices"].items() if dev.get("enabled", True)}
        sim = ModbusSimulator(devices)
        sim.set_holding(9, 2057, 5000)
        sim.set_holding(8, 8604, -1450)
        sim.set_input(3, 19, 120)
        self.assertEqual(sim.get_holding(9, 2057), 5000)
        self.assertEqual(sim.get_holding(8, 8604), 65536 - 1450)
        self.assertEqual(sim.get_input(3, 19), 120)
        self.assertEqual(sim.get_coil(3, 0), False)


if __name__ == "__main__":
    unittest.main()
