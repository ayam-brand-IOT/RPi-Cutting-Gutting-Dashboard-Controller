"""ATV320/FRENIC reads and calibration tests; no serial hardware required."""
import copy
import threading
import tempfile
from pathlib import Path
import yaml
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from config_loader import load_config
from modbus_manager import ModbusManager
from state import StateStore


class VFDTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config("config.yaml")
        self.devices = {name: self.cfg["devices"][name]
                        for name in ("vfd_infeed", "vfd_pocket", "gutting_left")}
        self.state = StateStore(list(self.devices))
        with patch("modbus_manager.ModbusSerialClient") as factory:
            self.manager = ModbusManager(self.cfg["machine"], self.devices,
                                         self.state, threading.Event())
            self.client = factory.return_value
        self.client.connected = True
        words = {3202: 500, 8604: 1450, 2057: 5000}
        self.client.read_holding_registers.side_effect = lambda **kw: SimpleNamespace(
            registers=[words[kw["address"]]], isError=lambda: False)

    def test_fc03_and_units(self):
        for name in ("vfd_infeed", "vfd_pocket"):
            self.manager._poll_device(name, self.devices[name])
        self.client.read_input_registers.assert_not_called()
        self.assertEqual([(c.kwargs["device_id"], c.kwargs["address"], c.kwargs["count"])
                          for c in self.client.read_holding_registers.call_args_list],
                         [(8, 3202, 1), (8, 8604, 1), (9, 2057, 1)])
        pocket = self.state.snapshot()["devices"]["vfd_pocket"]
        self.assertTrue(pocket["connected"])
        self.assertEqual(pocket["values"]["frequency_hz"], 50.0)
        self.assertNotIn("motor_rpm", pocket["values"])
        self.assertIsNone(pocket["values"]["pockets_per_min"])

    def test_pocket_conversion_and_reverse(self):
        device = copy.deepcopy(self.devices["vfd_pocket"])
        device["pockets_per_motor_revolution"] = 12 / 100
        self.assertEqual(self.manager._vfd_speed({"motor_rpm": 1450}, device),
                         {"pockets_per_min": 174.0})
        self.assertEqual(self.manager._vfd_speed({"motor_rpm": -1450}, device),
                         {"pockets_per_min": 174.0})
        self.assertEqual(self.manager._decode_input({3202: 65536 - 500},
                         self.devices["vfd_infeed"]["telemetry_holding_registers"]["frequency_hz"]), -50.0)
        for invalid in (0, -1, float("nan"), float("inf")):
            device["pockets_per_motor_revolution"] = invalid
            with self.assertRaises(ValueError):
                self.manager._vfd_speed({"motor_rpm": 1450}, device)

    def test_fuji_frequency_calibration(self):
        device = copy.deepcopy(self.devices["vfd_pocket"])
        device["pockets_per_min_per_hz"] = 3.48
        self.assertEqual(self.manager._vfd_speed({"frequency_hz": 50}, device),
                         {"pockets_per_min": 174.0})
        self.assertEqual(self.manager._vfd_speed({"frequency_hz": 0}, device),
                         {"pockets_per_min": 0.0})
        # Fuji frequency is unsigned, including values above 327.67 Hz.
        self.assertEqual(self.manager._decode_input({2057: 40000},
                         device["telemetry_holding_registers"]["frequency_hz"]), 400.0)
        # A previously calibrated RPM coefficient cannot invent Fuji motor RPM.
        device["pockets_per_motor_revolution"] = 0.12
        device["pockets_per_min_per_hz"] = None
        self.assertIsNone(self.manager._vfd_speed({"frequency_hz": 50}, device)["pockets_per_min"])

    def test_one_line_restore_and_profile_isolation(self):
        raw = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
        raw["devices"]["vfd_pocket"]["profile"] = "atv320"
        raw["devices"]["vfd_pocket"]["pockets_per_motor_revolution"] = 0.12
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text(yaml.safe_dump(raw), encoding="utf-8")
            restored = load_config(str(path))
            pocket = restored["devices"]["vfd_pocket"]
            self.assertEqual(pocket["slave"], 9)
            self.assertEqual(pocket["telemetry_holding_blocks"],
                             [{"address": 3202, "count": 1}, {"address": 8604, "count": 1}])
            self.assertEqual(self.manager._vfd_speed({"motor_rpm": 1450}, pocket),
                             {"pockets_per_min": 174.0})
            pocket["telemetry_holding_blocks"][0]["address"] = 999
            self.assertEqual(restored["devices"]["vfd_infeed"]["telemetry_holding_blocks"][0]["address"], 3202)
            self.assertEqual(restored["device_profiles"]["atv320"]["telemetry_holding_blocks"][0]["address"], 3202)
            raw["devices"]["vfd_pocket"]["profile"] = "typo"
            path.write_text(yaml.safe_dump(raw), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Profil inconnu"):
                load_config(str(path))

    def test_offline_isolation_and_short_response(self):
        self.manager._poll_device("vfd_infeed", self.devices["vfd_infeed"])
        self.client.read_holding_registers.side_effect = IOError("timeout")
        self.manager._poll_device("vfd_pocket", self.devices["vfd_pocket"])
        snapshot = self.state.snapshot()["devices"]
        self.assertFalse(snapshot["vfd_pocket"]["connected"])
        self.assertTrue(snapshot["vfd_infeed"]["connected"])
        self.client.read_holding_registers.side_effect = lambda **kw: SimpleNamespace(
            registers=[], isError=lambda: False)
        self.manager._poll_device("vfd_infeed", self.devices["vfd_infeed"])
        self.assertFalse(self.state.snapshot()["devices"]["vfd_infeed"]["connected"])

    def test_vfd_telemetry_cannot_be_written(self):
        self.manager.ack_callback = lambda result: setattr(self, "ack", result)
        self.manager._process_write({"device": "vfd_infeed", "parameter": "motor_rpm",
                                     "value": 1500, "request_id": "test"})
        self.assertEqual(self.ack["status"], "error")
        self.client.write_register.assert_not_called()
        self.client.write_coil.assert_not_called()

    def test_existing_fc04_telemetry(self):
        self.client.read_input_registers.return_value = SimpleNamespace(
            registers=list(range(20)), isError=lambda: False)
        self.manager._holding_next["gutting_left"] = float("inf")
        self.manager._poll_device("gutting_left", self.devices["gutting_left"])
        self.client.read_holding_registers.assert_not_called()
        self.assertEqual(self.state.snapshot()["devices"]["gutting_left"]["values"]["rpm_belt"], 19)


if __name__ == "__main__":
    unittest.main()
