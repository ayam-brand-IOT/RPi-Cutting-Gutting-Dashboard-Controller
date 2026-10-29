"""HSCS-380 observed FC03 captures, without serial hardware."""
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from config_loader import load_config
from modbus_manager import ModbusManager
from state import StateStore


class DehumidifierTests(unittest.TestCase):
    def setUp(self):
        cfg = load_config("config.yaml")
        self.device = cfg["devices"]["dehumidifier"]
        self.state = StateStore(["dehumidifier"])
        with patch("modbus_manager.ModbusSerialClient") as factory:
            self.manager = ModbusManager(cfg["machine"], {"dehumidifier": self.device},
                                         self.state, threading.Event())
            self.client = factory.return_value
        self.client.connected = True

    def poll(self, words):
        self.client.read_holding_registers.return_value = SimpleNamespace(
            registers=words, isError=lambda: False)
        self.manager._poll_device("dehumidifier", self.device)
        return self.state.snapshot()["devices"]["dehumidifier"]

    def test_observed_captures_and_read_only_map(self):
        self.assertTrue(self.device["enabled"])
        self.assertEqual(self.device["slave"], 1)
        self.assertEqual(self.device["holding_registers"], {})
        self.assertEqual(self.device["coils"], {})
        for packed, temperature, humidity in ((7228, 28, 60), (7482, 29, 58), (7736, 30, 56)):
            device = self.poll([21062, 258, 0, packed])
            self.assertTrue(device["connected"])
            values = device["values"]
            self.assertEqual((values["temperature_c"], values["humidity_pct"]),
                             (temperature, humidity))
            self.assertEqual((values["humidity_start_pct"], values["humidity_stop_pct"]), (82, 70))
            self.assertEqual((values["reported_slave"], values["baud_code"], values["baudrate"]),
                             (1, 2, 9600))
            self.assertEqual(values["diagnostic_raw"], 0)
            self.assertIsNone(values["running"])
            self.assertIsNone(values["fault_code"])
            self.assertEqual(values["alert_level"], "unknown")
            json.dumps(self.state.snapshot(), allow_nan=False)
        for call in self.client.read_holding_registers.call_args_list:
            self.assertEqual(call.kwargs, dict(device_id=1, address=0, count=4))
        self.client.read_input_registers.assert_not_called()
        self.client.read_coils.assert_not_called()
        self.client.write_register.assert_not_called()
        self.client.write_coil.assert_not_called()

    def test_nonzero_diagnostic_is_unconfirmed_warning(self):
        values = self.poll([21062, 258, 0x1234, 7736])["values"]
        self.assertEqual(values["diagnostic_raw"], 0x1234)
        self.assertEqual(values["alert_level"], "warning")
        self.assertIn("0x1234", values["alert_message"])
        self.assertIn("unconfirmed", values["alert_message"])
        self.assertIsNone(values["running"])
        self.assertIsNone(values["fault_code"])

    def test_short_or_failed_response_marks_offline(self):
        self.poll([21062, 258, 0, 7736])
        self.assertFalse(self.poll([21062, 258, 0])["connected"])
        self.client.read_holding_registers.side_effect = TimeoutError("timeout")
        self.manager._poll_device("dehumidifier", self.device)
        self.assertFalse(self.state.snapshot()["devices"]["dehumidifier"]["connected"])

    def test_invalid_measurement_bytes_not_presented_as_readings(self):
        values = self.poll([21062, 0x0109, 0, 0xFFFF])["values"]
        self.assertIsNone(values["temperature_c"])
        self.assertIsNone(values["humidity_pct"])
        self.assertIsNone(values["baudrate"])
        self.assertEqual(values["temperature_humidity_raw"], 65535)


if __name__ == "__main__":
    unittest.main()
