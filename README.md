# Cutting / Gutting Dashboard — Raspberry Pi Zero 2W

## 0. Overview

This project runs an **industrial gateway** on a Raspberry Pi Zero 2W for the Cutting/Gutting line:

- **Modbus RTU** (RS485) communicates with physical devices (gutting machines, sensors, etc.).
- **CP-IO22 GPIO** handles CIP (cleaning in place).
- **MQTT** is the central message bus: the RPi publishes the full state as JSON and receives commands.
- **Ecava IntegraXor (IGX)**, hosted on the SCADA server, uses only **two tags**: `machine_state_json` for reading and `machine_command_json` for writing. All machine details travel in the JSON payload rather than individual Ecava tags.
- A **Pygame operator dashboard** (`main.py` / `dashboard.py`) runs fullscreen without a desktop environment (kiosk mode). It displays production KPIs: fish/minute, Good/Bad/Belly, RPM, motor trips, CIP, water/electricity, weather and breaks.

This guide covers a complete installation on a **fresh Raspberry Pi Zero 2W**, followed by day-to-day operation.

**Guide order:**

1. Packages, virtual environment and manual `main.py` check
2. Kiosk mode (automatic startup without a desktop)
3. Serial port setup for the RS485 HAT
4. Development and testing tools
5. Operation and reference (SCADA, dashboard and troubleshooting)

---

## 1. Basic installation

Target: Raspberry Pi Zero 2W, Raspberry Pi OS (Debian Bookworm), user `lastra`, project at `~/dashboard-Cutting-Gutting`, virtual environment at `~/fish-venv`.

### 1.1 Why this approach

Some libraries (`pygame`, `gpiozero`) need the native system libraries (SDL2 with KMSDRM support, GPIO with `lgpio`) to work in kiosk mode. A regular `pip install` in an isolated virtual environment installs generic wheels **without** that support. Installing everything system-wide makes reproducibility and specific version constraints, such as `pymodbus>=3.6,<4`, harder to manage.

**Chosen approach:** install libraries with native bindings through `apt`, create the virtual environment with `--system-site-packages` so it can access them, and use `pip` for pure Python libraries that require a specific version.

### 1.2 System update and apt packages

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y \
  git \
  python3-venv \
  python3-pygame \
  python3-yaml \
  python3-serial \
  python3-paho-mqtt \
  python3-gpiozero \
  python3-lgpio
```

`python3-lgpio` is the recommended `gpiozero` pin factory on Bookworm. It avoids falling back to the experimental `NativeFactory` when no suitable pin factory is available.

### 1.3 Get the project

```bash
cd ~
git clone <repo-url> dashboard-Cutting-Gutting
cd ~/dashboard-Cutting-Gutting
```

Use the appropriate transfer method for your setup: Git clone, `scp`, USB drive, etc.

### 1.4 Create a virtual environment with access to system packages

```bash
python3 -m venv ~/fish-venv --system-site-packages
source ~/fish-venv/bin/activate
```

### 1.5 Install the remaining libraries with pip

```bash
pip install --upgrade pip
pip install "pymodbus>=3.6,<4"
```

Do **not** reinstall `pygame`, `PyYAML`, `pyserial`, `paho-mqtt` or `gpiozero` through pip: the virtual environment already sees the system packages through `--system-site-packages`.

### 1.6 Grant GPIO, serial and video access

```bash
sudo usermod -aG dialout,video,render,gpio lastra
```

Log out and back in, or reboot, for the new group memberships to take effect:

```bash
groups lastra
# Must include: dialout video render gpio
```

### 1.7 Verify the installation

```bash
python3 -c "import pygame; print('pygame SDL:', pygame.get_sdl_version())"
python3 -c "from gpiozero import Device; print('pin factory:', Device.pin_factory)"
python3 -c "import pymodbus; print('pymodbus:', pymodbus.__version__)"
```

Expected results:

- SDL matches the system version, rather than a separate version bundled in the virtual environment.
- The pin factory is `LGPIOFactory`, rather than `NativeFactory`.
- `pymodbus` satisfies `>=3.6,<4`.

### 1.8 Run main.py manually

Before configuring kiosk mode, check that the program starts from a normal console (desktop session, or SSH with a local display):

1. Review `config.yaml`: Modbus port, enabled devices, and the `mqtt`, `weather` and `dashboard` sections.
2. Run:
   ```bash
   cd ~/dashboard-Cutting-Gutting
   source ~/fish-venv/bin/activate
   python3 main.py --config config.yaml
   ```
3. Check the logs for Modbus and MQTT connections and JSON publication on `factory/cutting-gutting/scada/state`.

Proceed to kiosk mode only after this check succeeds.

---

## 2. Kiosk mode (automatic startup without a desktop)

### 2.1 Boot into the console

```bash
sudo raspi-config
```

Choose `System Options` → `Boot / Auto Login` → **Console Autologin**.

```bash
sudo reboot
```

After rebooting, check that no desktop processes are running:

```bash
htop
# Must NOT appear: Xwayland, labwc, wf-panel-pi, lxterminal
```

### 2.2 Access to graphics devices

```bash
sudo usermod -aG video,render,gpio,i2c,spi lastra
```

Section 1.6 already adds `video`, `render` and `gpio`. This command also adds `i2c` and `spi` if other peripherals need them.

Check that DRM devices exist:

```bash
ls -l /dev/dri
# Must list: card0, renderD128
```

### 2.3 KMSDRM rendering

For kiosk mode, SDL can be configured before importing `pygame`:

```python
os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")
```

`setdefault` gives priority to an environment variable already supplied by systemd. The current `dashboard.py` uses the SDL driver selected by its environment; it does not explicitly set this default or implement an automatic `fbcon` fallback. Set `SDL_VIDEODRIVER=kmsdrm` in the service environment when required by your installation.

### 2.4 Optional: disable headless screen sharing

If your Raspberry Pi OS Bookworm installation starts a headless Wayland compositor and VNC (`wayvnc`) in console mode, disable it when remote desktop access is unnecessary to free RAM and CPU:

```bash
systemctl list-units | grep -i vnc
sudo systemctl disable --now wayvnc.service
```

Adjust the service name to match the first command's output.

### 2.5 systemd service

```bash
sudo nano /etc/systemd/system/dashboard.service
```

```ini
[Unit]
Description=Dashboard Cutting/Gutting Production
After=multi-user.target

[Service]
Type=simple
User=lastra
WorkingDirectory=/home/lastra/dashboard-Cutting-Gutting
ExecStart=/home/lastra/fish-venv/bin/python3 /home/lastra/dashboard-Cutting-Gutting/main.py
Restart=on-failure
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

`ExecStart` must point to the virtual environment interpreter (`~/fish-venv/bin/python3`). systemd does not run `source activate`; supply the correct executable directly.

```bash
sudo systemctl daemon-reload
sudo systemctl enable dashboard.service
sudo systemctl start dashboard.service
```

### 2.6 Final checks

```bash
sudo systemctl status dashboard.service
journalctl -u dashboard.service -f
```

Example startup logs:

```
[DISPLAY] driver=KMSDRM resolution=1920x1080
[GPIO] ... ONLINE via gpiozero ...
```

```bash
htop
```

- No `Xwayland`, `labwc` or `lxterminal` processes.
- `python3 ... main.py` is the only significant display process.

To restart the service after changing code or configuration, without rebooting:

```bash
sudo systemctl restart dashboard.service
```

---

## 3. Enable the serial port for the RS485 HAT

The RS485 HAT uses the RPi hardware UART on GPIO14/TXD and GPIO15/RXD. On a fresh installation, the serial interface needs to be enabled; the full PL011 UART may also be assigned to Bluetooth.

### 3.1 Enable serial hardware using raspi-config

```bash
sudo raspi-config
```

Choose `Interface Options` → `Serial Port`.

- “Would you like a login shell to be accessible over serial?” → **No**. Otherwise, a getty process competes with Modbus for the port.
- “Would you like the serial port hardware to be enabled?” → **Yes**.

This adds `enable_uart=1` to `/boot/firmware/config.txt`.

### 3.2 Free the full UART by disabling Bluetooth

On the RPi Zero 2W, the main hardware UART (PL011, `ttyAMA0`) is assigned to Bluetooth by default. Only the mini-UART (`ttyS0`, less reliable at high baud rates) remains on the GPIO pins. To route the full PL011 UART to those pins for Modbus RTU:

```bash
sudo nano /boot/firmware/config.txt
```

Append:

```ini
dtoverlay=disable-bt
```

Then disable the service that manages Bluetooth over UART:

```bash
sudo systemctl disable hciuart
sudo reboot
```

### 3.3 Check after rebooting

```bash
ls -l /dev/serial0
# Must be a symbolic link to /dev/ttyAMA0

raspi-gpio get 14,15
# Must show GPIO 14/15 in ALT0 mode (TXD0/RXD0)
```

### 3.4 Test the serial link

With no device connected, perform a local loopback test: disconnect the HAT and jumper TX to RX on the connector.

```bash
python3 -c "
import serial, time
s = serial.Serial('/dev/serial0', 9600, timeout=1)
s.write(b'test')
time.sleep(0.2)
print(s.read(10))
"
```

With the RS485 HAT and a device connected to the bus, use `test_modbus_slave.py` (section 4.2) to verify communication end to end.

### 3.5 Configure the HAT and config.yaml

- Check the HAT's **120 Ω termination resistor** jumper or DIP switch. Enable termination only at a physical end of the RS485 bus.
- If the HAT controls DE/RE direction automatically, no additional GPIO is needed. For manual direction control, wire and configure the dedicated DE/RE pin.
- Set the enabled serial port in `config.yaml`:
  ```yaml
  modbus_port: /dev/serial0
  ```
- Check that user `lastra` belongs to `dialout` (section 1.6) and can open `/dev/serial0`.

---

## 4. Development and testing tools

These scripts run on a **development PC**, with a USB-RS485 adapter connected to the same bus as the devices.

### 4.1 simulator — Modbus RTU simulator

On Windows, launch the `simulator` executable. Its configuration file is `config.yaml` in `/dist`.

The GUI simulates enabled devices in `config.yaml` that are not listed in `REAL_DEVICES`. This lets you test `main.py` without connecting every physical device.

<!-- **simulator.py configuration (top of the file):**

```python
# Physical devices on the bus — excluded from the simulator
REAL_DEVICES: set[str] = {"gutting_left"}
```

**Start with a virtual serial pair** to test alongside main.py on the same machine using socat: -->

```bash
# Linux/RPi only
sudo apt install socat
socat -d -d pty,raw,echo=0 pty,raw,echo=0
# Example output: /dev/pts/3  and  /dev/pts/4
# → simulator on /dev/pts/3, config.yaml modbus_port: /dev/pts/4
```

In the GUI:

- **Serial port**: select the USB-RS485 adapter's COM or tty port.
- **Input Registers**: supplied by the simulator and read by the master.
- **Holding Registers / Coils**: read-only fields showing what `main.py` has written.
- **Active on bus** checkbox: uncheck it to stop the slave responding and simulate a missing device.

> [!NOTE]
> If a physical device is already present on the Modbus bus, disable its simulated counterpart in the application. Otherwise, address collisions may cause it to appear offline in the dashboard.

### 4.2 test_modbus_slave.py — Read a gutting device directly

Reads the gutting device registers once per second (slave 3 by default) and prints them in the terminal. Use it to check RS485 communication without running `main.py`.

```bash
python test_modbus_slave.py
```

Adjust these values at the top of the file if necessary:

```python
client = ModbusSerialClient(port="/dev/serial0", ...)  # or "COM3" on Windows
DEVICE_ID = 3  # slave address of the device under test
```

Typical output:

```
RPM blade/w1/w2 : 2450 / 1200 / 1180
Motor trip/on   : 0 / 1
Uptime (s)      : 3742
FW version      : 0x236
```

---

## 5. Operation and reference

### 5.1 Ecava integration — only two JSON tags

**State tag** (RPi → Ecava):

```text
IGX tag : machine_state_json
Topic   : factory/cutting-gutting/scada/state
Direction: MQTT Subscriber → tag IGX
Type    : string
```

The RPi publishes all values in one retained JSON message:

```json
{
  "timestamp": 1784700000,
  "rpi": {
    "cip": {
      "gutting_left": {
        "enable": true, "on_ms": 500, "off_ms": 8000, "output": false
      }
    }
  },
  "devices": {
    "gutting_left": {
      "connected": true,
      "values": {"rpm_blade": 2450, "motors_trip": 0},
      "parameters": {"eject_delay_ms": 200, "eject_enable": 1}
    }
  }
}
```

**Command tag** (Ecava → RPi):

```text
IGX tag : machine_command_json
Topic   : factory/cutting-gutting/scada/command
Direction: tag IGX → MQTT Publisher
Type    : string
Retain  : false
QoS     : 1
```

CIP command:

```json
{"target": "cip", "device": "gutting_left",
 "parameters": {"enable": 1, "on_ms": 500, "off_ms": 8000},
 "timestamp": 1784700000000}
```

Modbus command:

```json
{"target": "modbus", "device": "gutting_left",
 "parameters": {"eject_enable": 1, "eject_delay_ms": 200},
 "timestamp": 1784700000000}
```

RPi acknowledgement topic: `factory/cutting-gutting/scada/ack`.

The `timestamp` makes each command value unique so Ecava publishes successive commands even when their parameters are identical.

### 5.2 HTML interface (ecava_machine_control.html)

The page reads the JSON state directly. Commands must be Base64-encoded before calling IntegraXor's `setTag()`:

```javascript
getTag("machine_state_json")
setTag("machine_command_json", btoa(JSON.stringify(command)))
```

The RPi automatically detects and decodes `base64(JSON)`. It also accepts raw JSON sent from MQTT Explorer or another MQTT client.

The `*_actual` and `*_cmd` keys in JavaScript are internal UI identifiers. They are **not** additional tags to create in Ecava.

The operator interface is in English, including connection status, validation messages, break settings and counter reset controls.

### 5.3 Safety and Modbus

- MQTT commands are non-retained.
- Each value is validated before writing.
- Modbus registers are read back after writing.
- Only one thread accesses RS485.
- Waveshare CIP outputs are locked OFF.
- CP-IO22 outputs are forced OFF at startup and shutdown.
- Input Registers are read every second.
- Parameters are read every 10 seconds.

### 5.4 Pygame production dashboard

The English dashboard emphasizes operator KPIs: fish/minute, people present, Good, Bad and Belly ejections. Each gutting machine has a rolling productivity graph. Motor trips trigger a red banner, while RPM remains visible in the machine cards. The compact CIP area shows only the three Raspberry Pi / CP-IO22 outputs. Water and electricity appear at the bottom with icons and daily/monthly consumption.

Cutting machine feedback provisionally uses CP-IO22 inputs BCM20 (`cutting_motors_on`) and BCM21 (`cutting_motors_trip`), active HIGH. Its status appears near CIP, and a cutting motor trip immediately enters the global red alarm banner. Match the pins and polarity to the actual wiring.

Throughput is calculated from changes in `fish_counter` over `dashboard.productivity_window_s`. To display staffing from Modbus, add `people_count` to the chosen device's `input_registers`, then configure `dashboard.people_device` and `dashboard.people_key`.

Good, Bad and Belly are displayed as percentages of the corresponding total fish count, with the raw count below each percentage. The same calculation is used for the overall total and each gutting machine separately.


When the dashboard is launched in the foreground from an interactive SSH session, press **Space** in that terminal to save a screenshot immediately; Enter is not required. The same shortcut still works from a keyboard connected to the display.

When the dashboard runs as a systemd service, its standard input is not connected to SSH. Trigger a screenshot with:

```bash
sudo systemctl kill --signal=SIGUSR1 --kill-who=main dashboard.service
```

Files are written as timestamped PNGs to `dashboard.screenshot_dir` (`screenshots/` by default, relative to the service working directory). A confirmation appears for three seconds and the full path is written to the service log. Use `journalctl -u dashboard.service -n 20` to retrieve that path remotely.

### 5.5 Water and electricity meters

Two provisional devices are included in `config.yaml`: slaves 10 and 11, disabled by default. The reader supports `uint16`, `int16`, `uint32`, `int32` and `float32`, with `word_order`, `scale`, `offset` and `decimals`. Replace the addresses and types using the meter manuals, then set each device to `enabled: true`.

Daily/monthly values and connection status automatically appear in the dashboard and the MQTT JSON state at `factory/cutting-gutting/scada/state`. Ecava reads them through the same `machine_state_json` tag.

### 5.6 Time, weather and rate per worker

The dashboard displays a large clock. By default, the lightweight `WeatherManager` thread calls Open-Meteo every 10 minutes using the coordinates in `weather`, retrieving `temperature_2m` and `weather_code`. No API key is required. The supplied coordinates are for Taiping; replace them if the machine is elsewhere.

Weather can also be supplied through `mqtt.weather_topic` (default: `factory/cutting-gutting/weather`). The payload can be `{"temperature_c":27.4,"condition":"cloudy"}` or a plain temperature. Retained messages provide a value at startup.

Rate per worker is calculated in real time as `total_fish_per_minute / people_present`. It shows `--` when the people count is missing or zero. Until a Modbus register is available, publish the count as a retained message on `factory/cutting-gutting/people_count`, either as a scalar (`7`) or JSON (`{"people_count":7}`).

### 5.7 Breaks and counter reset

The four default break times are defined in `schedule.breaks`. Ecava sends updates with a `target: system` command. The RPi sorts and validates them, then saves them to `runtime_settings.json`. Pygame displays the next break and time remaining, automatically moving to the first break of the following day after the final break.

Ecava's reset command captures current counters as software offsets. It resets displayed statistics and graphs without writing Modbus registers or changing CIP settings. Offsets persist and are included in the MQTT state JSON.

### 5.8 Quick troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| `pygame.error: kmsdrm not available` in desktop mode | The compositor already owns the DRM device | Switch to Console Autologin (section 2.1) |
| `kmsdrm not available` even in console mode | pygame uses bundled SDL2 in a virtual environment without `--system-site-packages` | Repeat sections 1.2–1.4 |
| `fbcon not available` too | `/dev/dri` is missing or the KMS overlay is disabled | Check `dtoverlay=vc4-kms-v3d` in `/boot/firmware/config.txt` |
| `PinFactoryFallback` → `NativeFactory` | Missing `lgpio` / `RPi.GPIO` / `pigpio`, or an isolated virtual environment | Install `python3-lgpio` (section 1.2) and recreate the environment with `--system-site-packages` |
| No Modbus communication on `/dev/serial0` | UART is disabled or still assigned to Bluetooth | Repeat sections 3.1–3.3; check `dtoverlay=disable-bt` and that `hciuart` is disabled |
| `PermissionError` on `/dev/serial0` or `/dev/ttyAMA0` | The user is not in `dialout` | Run `sudo usermod -aG dialout lastra`, then log in again |
| RAM usage grows over several days | A leak in Modbus/MQTT reconnection logic outside `dashboard.py` | Inspect retry loops in `main.py` and the shared state module |
