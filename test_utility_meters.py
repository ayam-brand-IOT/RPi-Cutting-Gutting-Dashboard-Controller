"""Meter decoding and FC03 register maps, without serial hardware."""
import copy
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

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
        incomplete = copy.deepcopy(self.devices["water_meter"])
        incomplete["telemetry_holding_blocks"] = []
        incomplete["telemetry_holding_registers"] = {}
        self.manager._poll_device("water_meter", incomplete)
        device = self.state.snapshot()["devices"]["water_meter"]
        self.assertFalse(device["connected"])
        self.client.read_input_registers.assert_not_called()
        self.client.read_holding_registers.assert_not_called()

    def test_user_slave_address_preserved(self):
        # Test user choices in a temporary config, without imposing deployment defaults.
        raw = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            for enabled, slave in ((False, 11), (True, 51), (True, 37)):
                with self.subTest(enabled=enabled, slave=slave):
                    for name in self.devices:
                        raw["devices"][name]["enabled"] = enabled
                    raw["devices"]["water_meter"]["slave"] = slave
                    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
                    loaded = load_config(str(path))["devices"]
                    for name in self.devices:
                        self.assertEqual(loaded[name]["enabled"], enabled)
                    self.assertEqual(loaded["water_meter"]["slave"], slave)
                    self.assertEqual(loaded["water_meter"]["model"], "U-100B")

    def test_dae_fc03_integer_units_and_word_order(self):
        # Raw flow 1000 = 10 GPM; total exceeds 16 bits to check word order.
        total = 123456
        raw = {0: 0, 1: 1000, 2: total >> 16, 3: total & 65535,
               4: 1, 5: 2345, 6: 0x0118, 7: 123, 8: 2}
        self.client.read_holding_registers.side_effect = lambda **kw: SimpleNamespace(
            registers=[raw[a] for a in range(kw["address"], kw["address"] + kw["count"])],
            isError=lambda: False)
        self.manager._poll_device("water_meter", self.devices["water_meter"])
        self.client.read_input_registers.assert_not_called()
        self.assertEqual([(c.kwargs["address"], c.kwargs["count"])
                          for c in self.client.read_holding_registers.call_args_list],
                         [(0, 9)])
        for call in self.client.read_holding_registers.call_args_list:
            self.assertEqual(call.kwargs["device_id"], self.devices["water_meter"]["slave"])
        device = self.state.snapshot()["devices"]["water_meter"]
        self.assertTrue(device["connected"])
        values = device["values"]
        self.assertEqual(values["flow_l_min"], 37.854)
        self.assertEqual(values["water_total_m3"], round(total * 0.003785411784, 6))
        self.assertEqual(values["water_reverse_m3"], round(((1 << 16) | 2345) * 0.003785411784, 6))
        self.assertEqual(values["water_info_temperature_raw"], 0x0118)
        self.assertEqual(values["water_info"], 1)
        self.assertEqual(values["water_temperature_c"], 24)
        self.assertEqual(values["water_pressure_mpa"], 0.123)
        self.assertEqual(values["water_diagnostic_code"], 2)
        self.assertIsNone(values.get("water_today_m3"))
        self.assertIsNone(values.get("water_month_m3"))

    def test_dae_repeated_full_reads_and_signed_flow(self):
        for flow in (-1000, 0, 1000):
            with self.subTest(flow=flow):
                unsigned = flow & 0xFFFFFFFF
                self.client.read_holding_registers.return_value = SimpleNamespace(
                    registers=[unsigned >> 16, unsigned & 65535, 0, 0, 0, 0, 0, 0, 0],
                    isError=lambda: False)
                self.manager._poll_device("water_meter", self.devices["water_meter"])
                device = self.state.snapshot()["devices"]["water_meter"]
                self.assertTrue(device["connected"])
                self.assertEqual(device["values"]["flow_l_min"], round(flow * 0.03785411784, 3))
        self.assertEqual([c.kwargs for c in self.client.read_holding_registers.call_args_list],
                         [dict(device_id=self.devices["water_meter"]["slave"], address=0, count=9)] * 3)

    def test_dae_short_frame_stays_offline(self):
        self.client.read_holding_registers.return_value = SimpleNamespace(
            registers=[0] * 8, isError=lambda: False)
        self.manager._poll_device("water_meter", self.devices["water_meter"])
        device = self.state.snapshot()["devices"]["water_meter"]
        self.assertFalse(device["connected"])
        self.assertIn("incomplète", device["error"])


if __name__ == "__main__":
    unittest.main()
