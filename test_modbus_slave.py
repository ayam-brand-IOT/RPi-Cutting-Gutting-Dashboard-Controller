#!/usr/bin/env python3
"""
Test Modbus RTU en boucle - HAT RS485 Waveshare -> ESP32-S3 8DI8DO
Fish Cutting/Gutting Controller
"""

from pymodbus.client import ModbusSerialClient
import time

def main():
    client = ModbusSerialClient(
        port="/dev/serial0",
        baudrate=9600,
        parity="N",
        stopbits=1,
        bytesize=8,
        timeout=1
    )

    DEVICE_ID = 3

    if not client.connect():
        print("Impossible d'ouvrir le port série")
        exit(1)

    print("Port ouvert, lecture en boucle (Ctrl+C pour arrêter)...\n")

    try:
        while True:
            # Input registers 0..18 (télémétrie complète)
            rr = client.read_input_registers(address=0, count=19, device_id=DEVICE_ID)
            if rr.isError():
                print("Erreur input registers:", rr)
            else:
                regs = rr.registers
                print(f"RPM blade/w1/w2 : {regs[0]} / {regs[1]} / {regs[2]}")
                print(f"Inputs mask     : {bin(regs[3])}")
                print(f"Outputs mask    : {bin(regs[4])}")
                print(f"Ejector state   : {regs[5]}  (fires={regs[7]*65536 + regs[6]})")
                print(f"CIP state       : {regs[8]}")
                print(f"Motor trip/on   : {regs[9]} / {regs[10]}")
                print(f"Belt / Belly    : {regs[11]} / {regs[12]}")
                print(f"Alarm mask      : {bin(regs[13])}  unack={bin(regs[14])}")
                print(f"Sys state       : {regs[15]}")
                print(f"Uptime (s)      : {regs[17]*65536 + regs[16]}")
                print(f"FW version      : {hex(regs[18])}")

            # Discrete inputs (DI1..DI8)
            di = client.read_discrete_inputs(address=0, count=8, device_id=DEVICE_ID)
            if not di.isError():
                print(f"DI1-DI8         : {di.bits[:8]}")

            # Coils (EJECT_ENABLE, CIP_ENABLE, ALARM_ACK)
            co = client.read_coils(address=0, count=3, device_id=DEVICE_ID)
            if not co.isError():
                print(f"Coils           : eject={co.bits[0]} cip={co.bits[1]} ack={co.bits[2]}")

            print("-" * 50)
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nArrêt.")
    finally:
        client.close()

if __name__ == "__main__":
    main()
