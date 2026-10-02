from __future__ import annotations

import logging
import math
import queue
import struct
import threading
import time
import uuid

from pymodbus.client import ModbusSerialClient


# Les timeouts des slaves absents sont gérés et affichés par appareil.
for _log in ("pymodbus", "pymodbus.client", "pymodbus.client.serial",
             "pymodbus.transaction", "pymodbus.factory"):
    logging.getLogger(_log).setLevel(logging.CRITICAL)


class ModbusManager(threading.Thread):
    """Maître RTU : chaque slave est interrogé indépendamment."""

    def __init__(self, machine_cfg, devices, state, stop_event):
        super().__init__(name="modbus", daemon=True)
        self.cfg = machine_cfg
        self.devices = devices
        self.state = state
        self.stop_event = stop_event
        self.write_queue: queue.Queue[dict] = queue.Queue(maxsize=100)
        self.ack_callback = lambda payload: None
        self._online = {name: False for name in devices}
        self._holding_next = {name: 0.0 for name in devices}
        self._holding_error = {name: "" for name in devices}
        self.client = ModbusSerialClient(
            port=self.cfg["modbus_port"],
            baudrate=int(self.cfg.get("modbus_baud", 9600)),
            bytesize=8,
            parity=str(self.cfg.get("modbus_parity", "N")),
            stopbits=int(self.cfg.get("modbus_stopbits", 1)),
            timeout=float(self.cfg.get("modbus_timeout_s", 0.4)),
            retries=0,
        )

    @staticmethod
    def _call(method, slave, **kwargs):
        """Compatibilité pymodbus utilisant device_id ou slave."""
        try:
            return method(device_id=slave, **kwargs)
        except TypeError:
            return method(slave=slave, **kwargs)

    def _ensure_connection(self):
        try:
            return self.client.connected or self.client.connect()
        except Exception:
            return False

    def _set_offline(self, name, error):
        if self._online[name]:
            print(f"[MODBUS] OFFLINE {name}: {error}", flush=True)
        self._online[name] = False
        self.state.update_device(name, False, error=str(error))

    def _set_online(self, name, values):
        if not self._online[name]:
            print(f"[MODBUS] ONLINE {name}", flush=True)
        self._online[name] = True
        self.state.update_device(name, True, values, "")

    def enqueue_write(self, request):
        request = dict(request)
        if not request.get("request_id"):
            request["request_id"] = str(uuid.uuid4())
        self.write_queue.put_nowait(request)
        return request["request_id"]

    def _poll_device(self, name, device):
        """Une erreur ne modifie que l'état du slave concerné."""
        if not self._ensure_connection():
            self._set_offline(name, "port série indisponible")
            return

        slave = int(device["slave"])
        raw = {}
        try:
            blocks = [(block, self.client.read_input_registers)
                      for block in device.get("input_blocks", [])]
            # FC03 telemetry is separate from writable settings (drives/meters).
            blocks += [(block, self.client.read_holding_registers)
                       for block in device.get("telemetry_holding_blocks", [])]
            if not blocks:
                raise ValueError("aucun registre de télémétrie configuré")
            for block, reader in blocks:
                response = self._call(
                    reader,
                    slave,
                    address=int(block["address"]),
                    count=int(block["count"]),
                )
                if response.isError():
                    raise IOError(str(response))
                if len(response.registers) != int(block["count"]):
                    raise IOError("réponse Modbus incomplète")
                start = int(block["address"])
                for offset, value in enumerate(response.registers):
                    raw[start + offset] = value

            values = {
                key: self._decode_input(raw, spec)
                for key, spec in {
                    **device.get("input_registers", {}),
                    **device.get("telemetry_holding_registers", {}),
                }.items()
            }
            if device.get("type") == "vfd":
                values.update(self._vfd_speed(values, device))
            if device.get("meter_profile") == "acrel_adl400":
                values.update(self._acrel_values(values))
            if "ejector_count_lo" in values:
                values["ejector_count"] = (
                    values["ejector_count_lo"]
                    | (values.get("ejector_count_hi", 0) << 16)
                )
            if "uptime_lo" in values:
                values["uptime_s"] = (
                    values["uptime_lo"]
                    | (values.get("uptime_hi", 0) << 16)
                )
            self._set_online(name, values)
            if time.monotonic() >= self._holding_next[name]:
                self._poll_holding(name, device, slave)
                self._holding_next[name] = (
                    time.monotonic() + float(self.cfg.get("holding_poll_s", 10.0))
                )
        except Exception as error:
            self._set_offline(name, error)

    @staticmethod
    def _acrel_values(values):
        """ADL400 integer readings are secondary-side; apply configured PT and CT."""
        pt, ct = values["pt_ratio"], values["ct_ratio"]
        if not 1 <= pt <= 9999 or not 1 <= ct <= 9999:
            raise ValueError("ADL400: rapports PT/CT invalides")
        ratio = pt * ct
        return {
            "power_kw": round(values["power_secondary_kw"] * ratio, 3),
            "energy_total_kwh": round(values["energy_import_secondary_kwh"] * ratio, 2),
            # Lifetime index is not today's/month's consumption.
            "energy_today_kwh": None,
            "energy_month_kwh": None,
        }

    @staticmethod
    def _vfd_speed(values, device):
        """Prefer drive motor RPM; frequency-only drives need a Hz calibration."""
        rpm = values.get("motor_rpm")
        factor = device.get("pockets_per_motor_revolution") if rpm is not None else None
        source = rpm
        if factor is None:
            factor = device.get("pockets_per_min_per_hz")
            source = values.get("frequency_hz")
        if factor is None:
            return {"pockets_per_min": None}
        factor = float(factor)
        if not math.isfinite(factor) or factor <= 0:
            raise ValueError("VFD: coefficient de vitesse positif et fini requis")
        if source is None:
            return {"pockets_per_min": None}
        return {"pockets_per_min": round(abs(source) * factor, 1)}

    @staticmethod
    def _decode_input(raw, spec):
        """Décode un registre simple ou un uint32/int32/float32 configurable."""
        if not isinstance(spec, dict):
            return raw.get(int(spec), 0)

        address = int(spec["address"])
        data_type = str(spec.get("data_type", "uint16")).lower()
        scale = float(spec.get("scale", 1.0))
        offset = float(spec.get("offset", 0.0))

        if data_type in ("uint16", "int16"):
            value = int(raw.get(address, 0))
            if data_type == "int16" and value >= 0x8000:
                value -= 0x10000
        elif data_type in ("uint32", "int32", "float32"):
            words = [int(raw.get(address, 0)), int(raw.get(address + 1, 0))]
            if str(spec.get("word_order", "big")).lower() == "little":
                words.reverse()
            packed = struct.pack(">HH", *words)
            if data_type == "float32":
                value = struct.unpack(">f", packed)[0]
            elif data_type == "int32":
                value = struct.unpack(">i", packed)[0]
            else:
                value = struct.unpack(">I", packed)[0]
        else:
            raise ValueError(f"data_type non supporté: {data_type}")

        value = value * scale + offset
        decimals = spec.get("decimals")
        if decimals is not None:
            value = round(value, int(decimals))
        if data_type.startswith(("uint", "int")) and scale == 1.0 and offset == 0.0:
            return int(value)
        return value

    @staticmethod
    def _parameter_blocks(specs):
        addresses = sorted({int(spec["address"]) for spec in specs.values()})
        if not addresses:
            return []
        blocks, start, previous = [], addresses[0], addresses[0]
        for address in addresses[1:]:
            if address != previous + 1:
                blocks.append((start, previous - start + 1))
                start = address
            previous = address
        blocks.append((start, previous - start + 1))
        return blocks

    def _poll_holding(self, name, device, slave):
        holding = device.get("holding_registers", {})
        coils = device.get("coils", {})
        if not holding and not coils:
            return
        raw = {}
        try:
            for address, count in self._parameter_blocks(holding):
                response = self._call(
                    self.client.read_holding_registers, slave,
                    address=address, count=count,
                )
                if response.isError():
                    raise IOError(str(response))
                for offset, value in enumerate(response.registers):
                    raw[address + offset] = value
            parameters = {
                key: raw[int(spec["address"])] for key, spec in holding.items()
            }
            raw_coils = {}
            for address, count in self._parameter_blocks(coils):
                response = self._call(
                    self.client.read_coils, slave,
                    address=address, count=count,
                )
                if response.isError():
                    raise IOError(str(response))
                for offset in range(count):
                    raw_coils[address + offset] = bool(response.bits[offset])
            parameters.update({
                key: raw_coils[int(spec["address"])] for key, spec in coils.items()
            })
            self.state.update_parameters(name, parameters, "")
            self._holding_error[name] = ""
        except Exception as error:
            message = str(error)
            if message != self._holding_error[name]:
                print(f"[MODBUS] paramètres indisponibles {name}: {message}", flush=True)
            self._holding_error[name] = message
            self.state.update_parameters(name, error=message)

    def _process_write(self, request):
        name = request["device"]
        parameter = request["parameter"]
        value = request["value"]
        result = {
            "request_id": request["request_id"],
            "device": name,
            "parameter": parameter,
            "requested_value": value,
        }
        try:
            if name not in self.devices:
                raise KeyError("device inconnu")
            if not self._ensure_connection():
                raise ConnectionError("port série indisponible")

            device = self.devices[name]
            slave = int(device["slave"])
            holding = device.get("holding_registers", {})
            coils = device.get("coils", {})

            if parameter in holding:
                address = int(holding[parameter]["address"])
                response = self._call(
                    self.client.write_register, slave,
                    address=address, value=int(value),
                )
                if response.isError():
                    raise IOError(str(response))
                self.stop_event.wait(0.05)
                check = self._call(
                    self.client.read_holding_registers, slave,
                    address=address, count=1,
                )
                if check.isError():
                    raise IOError(str(check))
                result.update(status="accepted", readback_value=check.registers[0])

            elif parameter in coils:
                address = int(coils[parameter]["address"])
                response = self._call(
                    self.client.write_coil, slave,
                    address=address, value=bool(value),
                )
                if response.isError():
                    raise IOError(str(response))
                self.stop_event.wait(0.05)
                check = self._call(
                    self.client.read_coils, slave,
                    address=address, count=1,
                )
                if check.isError():
                    raise IOError(str(check))
                result.update(status="accepted", readback_value=bool(check.bits[0]))
            else:
                raise KeyError("paramètre inconnu")
        except Exception as error:
            result.update(status="error", reason=str(error))
        self.ack_callback(result)

    def run(self):
        period = float(self.cfg.get("modbus_poll_s", 1.0))
        silence = float(self.cfg.get("modbus_silence_s", 0.05))
        print(
            f"[MODBUS] {self.cfg['modbus_port']} "
            f"{self.cfg.get('modbus_baud', 9600)} bauds",
            flush=True,
        )
        try:
            while not self.stop_event.is_set():
                started = time.monotonic()

                # Les commandes MQTT sont prioritaires mais restent dans ce
                # thread : aucun autre thread ne touche au port série.
                while True:
                    try:
                        self._process_write(self.write_queue.get_nowait())
                        self.stop_event.wait(silence)
                    except queue.Empty:
                        break

                for name, device in self.devices.items():
                    if self.stop_event.is_set():
                        break
                    self._poll_device(name, device)
                    self.stop_event.wait(silence)

                elapsed = time.monotonic() - started
                self.stop_event.wait(max(0.05, period - elapsed))
        finally:
            self.client.close()
