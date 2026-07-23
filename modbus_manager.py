from __future__ import annotations

import logging
import queue
import threading
import time
import uuid

from pymodbus.client import ModbusSerialClient


# Les timeouts des slaves absents sont gérés et affichés par appareil.
logging.getLogger("pymodbus").setLevel(logging.ERROR)


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
            parity="N",
            stopbits=1,
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
            for block in device.get("input_blocks", []):
                response = self._call(
                    self.client.read_input_registers,
                    slave,
                    address=int(block["address"]),
                    count=int(block["count"]),
                )
                if response.isError():
                    raise IOError(str(response))
                start = int(block["address"])
                for offset, value in enumerate(response.registers):
                    raw[start + offset] = value

            values = {
                key: raw.get(int(address), 0)
                for key, address in device.get("input_registers", {}).items()
            }
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
