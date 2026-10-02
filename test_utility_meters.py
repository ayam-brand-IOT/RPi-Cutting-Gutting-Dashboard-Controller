"""Meter decoding and incomplete DAE setup, without serial hardware."""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from config_loader import load_config
from modbus_manager import ModbusManager
from state import StateStore


class UtilityMeterTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config("config.yaml")
        self.devices = {name: self.cfg["devices"][name]
                        for name in ("electricity_meter", "water_meter")}
        self.state = StateStore(list(self.devices))
        with patch("modbus_manager.ModbusSerialClient") as factory:
            self.manager = ModbusManager(self.cfg["machine"], self.devices,
                                         self.state, threading.Event())
            self.client = factory.return_value
        self.client.connected = True

    def test_acrel_fc03_units_and_ratios(self):
        # 1234.56 secondary kWh, -12.3456 secondary kW, PT=2 CT=10.
        energy, power = 123456, (-123456) & 0xFFFFFFFF
        raw = {10: energy >> 16, 11: energy & 65535, 141: 2, 142: 10,
               362: power >> 16, 363: power & 65535}
        self.client.read_holding_registers.side_effect = lambda **kw: SimpleNamespace(
            registers=[raw[a] for a in range(kw["address"], kw["address"] + kw["count"])],
            isError=lambda: False)
        self.manager._poll_device("electricity_meter", self.devices["electricity_meter"])
        self.client.read_input_registers.assert_not_called()
        calls = self.client.read_holding_registers.call_args_list
        self.assertEqual([(c.kwargs["address"], c.kwargs["count"]) for c in calls],
                         [(10, 2), (141, 2), (362, 2)])
        device = self.state.snapshot()["devices"]["electricity_meter"]
        self.assertTrue(device["connected"])
        self.assertEqual(device["values"]["energy_total_kwh"], 24691.2)
        self.assertEqual(device["values"]["power_kw"], -246.912)
        self.assertIsNone(device["values"]["energy_today_kwh"])
        self.assertIsNone(device["values"]["energy_month_kwh"])

    def test_invalid_pt_ct(self):
        for ratio in (0, 10000):
            with self.assertRaises(ValueError):
                self.manager._acrel_values({"pt_ratio": ratio, "ct_ratio": 1,
                    "power_secondary_kw": 1, "energy_import_secondary_kwh": 1})

    def test_dae_empty_map_stays_offline(self):
        self.manager._poll_device("water_meter", self.devices["water_meter"])
        device = self.state.snapshot()["devices"]["water_meter"]
        self.assertFalse(device["connected"])
        self.client.read_input_registers.assert_not_called()
        self.client.read_holding_registers.assert_not_called()

    def test_user_slave_address_preserved(self):
        for name in self.devices:
            self.assertFalse(self.devices[name]["enabled"])
        self.assertEqual(self.devices["water_meter"]["slave"], 11)
        self.assertEqual(self.devices["water_meter"]["model"], "U-100B")


if __name__ == "__main__":
    unittest.main()
