from __future__ import annotations

import collections
import datetime
import functools
import sys
import time

try:
    import select
    import termios
    import tty
except ImportError:  # Non-POSIX development machines
    select = termios = tty = None
from pathlib import Path

import pygame


BG = (7, 13, 23)
PANEL = (16, 27, 43)
PANEL_2 = (12, 22, 36)
LINE = (39, 56, 77)
TEXT = (239, 245, 251)
MUTED = (137, 154, 174)
GREEN = (43, 210, 145)
BLUE = (68, 158, 255)
CYAN = (57, 211, 221)
AMBER = (255, 181, 68)
RED = (247, 78, 91)
RED_DARK = (75, 24, 35)


@functools.lru_cache(maxsize=64)
def _font(size, bold=False):
    return pygame.font.SysFont("DejaVu Sans", max(10, int(size)), bold=bold)


def _text(screen, value, position, size=18, color=TEXT, bold=False,
          anchor="topleft"):
    image = _font(size, bold).render(str(value), True, color)
    rect = image.get_rect()
    setattr(rect, anchor, position)
    screen.blit(image, rect)
    return rect


def _panel(screen, rect, color=PANEL, border=LINE, radius=16):
    pygame.draw.rect(screen, color, rect, border_radius=radius)
    pygame.draw.rect(screen, border, rect, 1, border_radius=radius)


def _pill(screen, rect, label, active=False, fault=False):
    if fault:
        bg, fg = RED_DARK, RED
    elif active:
        bg, fg = (17, 60, 49), GREEN
    else:
        bg, fg = (31, 45, 62), MUTED
    pygame.draw.rect(screen, bg, rect, border_radius=rect.h // 2)
    _text(screen, label, rect.center, 12, fg, True, "center")


def _value(device, key, default=0):
    return device.get("values", {}).get(key, default)


def _device(snapshot, name):
    return snapshot.get("devices", {}).get(name, {})


def _adjusted_counter(snapshot, device_name, key, raw_value):
    raw = max(0, int(raw_value))
    offset = int(
        snapshot.get("rpi", {})
        .get("counter_offsets", {})
        .get(device_name, {})
        .get(key, 0)
    )
    return raw - offset if raw >= offset else raw


def _next_break(breaks):
    now = datetime.datetime.now()
    valid = []
    for value in breaks or []:
        try:
            hour, minute = (int(part) for part in str(value).split(":", 1))
            valid.append((hour, minute))
        except (TypeError, ValueError):
            continue
    if not valid:
        return "--:--", "not configured"
    valid.sort()
    target = None
    tomorrow = False
    for hour, minute in valid:
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate >= now:
            target = candidate
            break
    if target is None:
        hour, minute = valid[0]
        target = (now + datetime.timedelta(days=1)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        tomorrow = True
    remaining = max(0, int((target - now).total_seconds() // 60))
    duration = f"{remaining // 60}h{remaining % 60:02d}" if remaining >= 60 else f"{remaining} min"
    return target.strftime("%H:%M"), ("tomorrow • " if tomorrow else "") + f"in {duration}"


class ProductivityMeter:
    """Calculate a rolling throughput rate from cumulative counters."""

    def __init__(self, window_s=60.0):
        self.window_s = float(window_s)
        self.samples = collections.deque()
        self.history = collections.deque(maxlen=90)
        self.last_total = None
        self.last_history_at = 0.0

    def update(self, total, now=None):
        now = time.monotonic() if now is None else now
        total = max(0, int(total))
        if self.last_total is not None and total < self.last_total:
            self.samples.clear()
        self.last_total = total
        if not self.samples or total != self.samples[-1][1]:
            self.samples.append((now, total))
        while len(self.samples) > 1 and now - self.samples[0][0] > self.window_s:
            self.samples.popleft()
        rate = 0.0
        if len(self.samples) >= 2:
            elapsed = self.samples[-1][0] - self.samples[0][0]
            if elapsed > 0:
                rate = max(0.0, (self.samples[-1][1] - self.samples[0][1]) * 60.0 / elapsed)
        if now - self.last_history_at >= 2.0:
            self.history.append(rate)
            self.last_history_at = now
        return rate

    def points(self):
        return list(self.history)


def _people_count(snapshot, config):
    """Look up the configured counter, then common aliases."""
    device_name = config.get("people_device")
    key = config.get("people_key", "people_count")
    if device_name:
        value = _value(_device(snapshot, device_name), key, None)
        if value is not None:
            return int(value)
    aliases = ("people_count", "persons_present", "person_count", "people_present")
    rpi = snapshot.get("rpi", {})
    for alias in aliases:
        if alias in rpi:
            return int(rpi[alias])
    for device in snapshot.get("devices", {}).values():
        for alias in aliases:
            if alias in device.get("values", {}):
                return int(device["values"][alias])
    return None


def _kpi(screen, rect, title, value, subtitle, color=TEXT, hero=False):
    _panel(screen, rect)
    compact = rect.w < 180
    _text(screen, title.upper(), (rect.x + 14, rect.y + 15),
          10 if compact else 12, MUTED, True)
    _text(screen, value, (rect.centerx, rect.centery + (8 if hero else 3)),
          (50 if compact else 56) if hero else (29 if compact else 34),
          color, True, "center")
    _text(screen, subtitle, (rect.centerx, rect.bottom - 17),
          9 if compact else 11, MUTED,
          False, "midbottom")


def _people_kpi(screen, rect, people, cadence):
    _panel(screen, rect)
    _text(screen, "PEOPLE", (rect.x + 14, rect.y + 15), 10, MUTED, True)
    _text(screen, "--" if people is None else people,
          (rect.centerx, rect.y + 55), 32, CYAN if people is not None else MUTED,
          True, "center")
    pygame.draw.line(screen, LINE, (rect.x + 14, rect.y + 83),
                     (rect.right - 14, rect.y + 83), 1)
    _text(screen, "RATE / WORKER", (rect.centerx, rect.y + 99),
          9, MUTED, True, "center")
    cadence_text = "--" if cadence is None else f"{cadence:.1f}"
    _text(screen, cadence_text, (rect.centerx, rect.y + 121),
          19, BLUE if cadence is not None else MUTED, True, "center")
    _text(screen, "fish/min", (rect.centerx, rect.bottom - 6),
          8, MUTED, False, "midbottom")


def _quality_kpi(screen, rect, title, count, total, color):
    _panel(screen, rect)
    compact = rect.w < 180
    percentage = (100.0 * count / total) if total > 0 else 0.0
    _text(screen, title.upper(), (rect.x + 14, rect.y + 15),
          10 if compact else 12, MUTED, True)
    _text(screen, f"{percentage:.1f}%", (rect.centerx, rect.centery + 2),
          27 if compact else 32, color, True, "center")
    _text(screen, f"{count} fish", (rect.centerx, rect.bottom - 17),
          9 if compact else 11, MUTED, False, "midbottom")


def _alarm_banner(screen, rect, trips, offline):
    if trips:
        pygame.draw.rect(screen, RED_DARK, rect, border_radius=14)
        pygame.draw.rect(screen, RED, rect, 2, border_radius=14)
        _text(screen, "⚠  MOTOR TRIP", (rect.x + 20, rect.centery), 22,
              RED, True, "midleft")
        _text(screen, "  •  ".join(trips), (rect.right - 20, rect.centery),
              18, TEXT, True, "midright")
    elif offline:
        pygame.draw.rect(screen, (68, 50, 22), rect, border_radius=14)
        pygame.draw.rect(screen, AMBER, rect, 1, border_radius=14)
        _text(screen, "COMMUNICATION DEGRADED", (rect.x + 20, rect.centery),
              17, AMBER, True, "midleft")
        _text(screen, "  •  ".join(offline), (rect.right - 20, rect.centery),
              15, TEXT, False, "midright")
    else:
        pygame.draw.rect(screen, (14, 48, 41), rect, border_radius=14)
        pygame.draw.rect(screen, (26, 92, 73), rect, 1, border_radius=14)
        _text(screen, "✓  NORMAL PRODUCTION", (rect.x + 20, rect.centery),
              17, GREEN, True, "midleft")
        _text(screen, "No motor trips", (rect.right - 20, rect.centery),
              14, MUTED, False, "midright")


def _sparkline(screen, rect, values, color):
    pygame.draw.rect(screen, PANEL_2, rect, border_radius=9)
    pygame.draw.rect(screen, LINE, rect, 1, border_radius=9)
    for index in (1, 2):
        y = rect.y + rect.h * index // 3
        pygame.draw.line(screen, (27, 42, 59), (rect.x + 7, y),
                         (rect.right - 7, y), 1)
    if len(values) < 2:
        _text(screen, "Collecting…", rect.center, 11, MUTED, False, "center")
        return
    maximum = max(1.0, max(values) * 1.12)
    count = len(values)
    points = []
    for index, value in enumerate(values):
        x = rect.x + 7 + int((rect.w - 14) * index / max(1, count - 1))
        y = rect.bottom - 7 - int((rect.h - 14) * value / maximum)
        points.append((x, y))
    if len(points) > 1:
        pygame.draw.lines(screen, color, False, points, 2)
    pygame.draw.circle(screen, color, points[-1], 3)


def _machine_card(screen, rect, title, device, productivity, history, stats):
    values = device.get("values", {})
    online = bool(device.get("connected"))
    trip = bool(values.get("motors_trip"))
    color = RED_DARK if trip else PANEL
    border = RED if trip else LINE
    _panel(screen, rect, color, border)
    _text(screen, title, (rect.x + 16, rect.y + 14), 17, TEXT, True)
    status_rect = pygame.Rect(rect.right - 112, rect.y + 11, 96, 27)
    if trip:
        _pill(screen, status_rect, "MOTOR TRIP", fault=True)
    elif not online:
        _pill(screen, status_rect, "OFFLINE", fault=True)
    else:
        _pill(screen, status_rect, "ONLINE", active=True)

    stats_width = rect.w - 20
    total = stats["total"]
    stat_items = (("DAILY TOTAL", stats["total"], None, BLUE),
                  ("GOOD", stats["good"],
                   100.0 * stats["good"] / total if total else 0.0, GREEN),
                  ("BAD", stats["bad"],
                   100.0 * stats["bad"] / total if total else 0.0,
                   RED if stats["bad"] else MUTED),
                  ("BELLY", stats["belly"],
                   100.0 * stats["belly"] / total if total else 0.0, AMBER))
    for index, (label, count, percentage, stat_color) in enumerate(stat_items):
        cx = rect.x + 10 + int(stats_width * (index + 0.5) / 4)
        _text(screen, label, (cx, rect.y + 45), 9, MUTED, True, "center")
        main_value = str(count) if percentage is None else f"{percentage:.1f}%"
        _text(screen, main_value, (cx, rect.y + 66), 18, stat_color, True, "center")
        _text(screen, "fish" if percentage is None else f"{count} fish",
              (cx, rect.y + 84), 8, MUTED, False, "center")

    motor_on = bool(values.get("motors_on"))
    belt_on = bool(values.get("belt_on"))
    _text(screen, f"PRODUCTIVITY  {productivity:.1f} fish/min",
          (rect.x + 14, rect.y + 101), 10, BLUE, True, "midleft")
    _text(screen, "MOTORS " + ("ON" if motor_on else "OFF"),
          (rect.centerx - 42, rect.y + 101), 9,
          GREEN if motor_on else MUTED, True, "midright")
    _text(screen, "BELT " + ("ON" if belt_on else "OFF"),
          (rect.centerx + 5, rect.y + 101), 9,
          GREEN if belt_on else MUTED, True, "midleft")
    _text(screen, "RPM  {} / {} / {}".format(
        values.get("rpm_blade", 0), values.get("rpm_wheel1", 0),
        values.get("rpm_wheel2", 0)),
        (rect.right - 14, rect.y + 101), 9, MUTED, False, "midright")

    graph = pygame.Rect(rect.x + 13, rect.y + 114, rect.w - 26, rect.h - 125)
    _sparkline(screen, graph, history, BLUE)


def _cip_card(screen, rect, title, item):
    enabled = bool(item.get("enable"))
    output = bool(item.get("output"))
    _panel(screen, rect, (15, 31, 45) if output else PANEL_2,
           GREEN if output else LINE)
    pygame.draw.circle(screen, GREEN if output else (60, 76, 95),
                       (rect.x + 17, rect.centery), 6)
    _text(screen, title, (rect.x + 30, rect.centery), 13, TEXT, True, "midleft")
    _pill(screen, pygame.Rect(rect.centerx - 36, rect.centery - 12, 72, 24),
          "ACTIVE" if output else ("READY" if enabled else "OFF"),
          active=enabled or output)
    _text(screen, f"ON {item.get('on_ms', '--')} ms  •  OFF {item.get('off_ms', '--')} ms",
          (rect.right - 12, rect.centery), 9, MUTED, False, "midright")


def _cutting_status(screen, rect, gpio, gpio_error=""):
    inputs_available = (
        "cutting_motors_on" in gpio and "cutting_motors_trip" in gpio
    )
    motor_on = bool(gpio.get("cutting_motors_on")) if inputs_available else False
    trip = bool(gpio.get("cutting_motors_trip")) if inputs_available else False
    _panel(screen, rect, RED_DARK if trip else PANEL_2, RED if trip else LINE, 11)
    _text(screen, "CUTTING MACHINE", (rect.x + 20, rect.centery),
          18, TEXT, True, "midleft")
    _text(screen, "MOTOR STATUS", (rect.centerx - 90, rect.y + 12),
          10, MUTED, True, "center")
    _pill(screen, pygame.Rect(rect.centerx - 140, rect.y + 25, 100, 27),
          ("MOTORS ON" if motor_on else "MOTORS OFF") if inputs_available else "NOT CONFIG",
          active=motor_on)
    _text(screen, "SAFETY", (rect.centerx + 90, rect.y + 12),
          10, MUTED, True, "center")
    _pill(screen, pygame.Rect(rect.centerx + 45, rect.y + 25, 90, 27),
          ("TRIP ACTIVE" if trip else "TRIP OK") if inputs_available else "TRIP --",
          active=inputs_available and not trip, fault=trip)
    if gpio_error:
        _text(screen, "GPIO OFFLINE", (rect.right - 20, rect.centery),
              13, AMBER, True, "midright")
    else:
        _text(screen, "CP-IO22", (rect.right - 20, rect.centery),
              12, MUTED, True, "midright")


def _draw_drop(screen, center, color):
    x, y = center
    points = [(x, y - 15), (x - 10, y + 1), (x - 8, y + 10),
              (x, y + 15), (x + 8, y + 10), (x + 10, y + 1)]
    pygame.draw.polygon(screen, color, points)


def _draw_bolt(screen, center, color):
    x, y = center
    points = [(x + 3, y - 16), (x - 9, y + 2), (x - 1, y + 2),
              (x - 5, y + 16), (x + 11, y - 5), (x + 3, y - 5)]
    pygame.draw.polygon(screen, color, points)


def _draw_weather_icon(screen, center, condition):
    x, y = center
    condition = str(condition or "unknown").lower()
    rainy = any(word in condition for word in ("rain", "pluie", "storm", "orage"))
    cloudy = rainy or any(
        word in condition for word in ("cloud", "nuage", "overcast", "snow", "neige")
    )
    if not cloudy:
        pygame.draw.circle(screen, AMBER, (x, y), 10)
        for dx, dy in ((0, -17), (0, 17), (-17, 0), (17, 0),
                       (-12, -12), (12, -12), (-12, 12), (12, 12)):
            pygame.draw.line(screen, AMBER, (x + dx * 2 // 3, y + dy * 2 // 3),
                             (x + dx, y + dy), 2)
        return
    cloud = (190, 204, 218)
    pygame.draw.circle(screen, cloud, (x - 8, y + 1), 9)
    pygame.draw.circle(screen, cloud, (x + 2, y - 4), 12)
    pygame.draw.circle(screen, cloud, (x + 13, y + 2), 8)
    pygame.draw.rect(screen, cloud, (x - 16, y, 36, 10), border_radius=5)
    if rainy:
        for dx in (-10, 0, 10):
            pygame.draw.line(screen, BLUE, (x + dx, y + 13),
                             (x + dx - 3, y + 20), 2)


def _weather_widget(screen, rect, weather):
    _panel(screen, rect, PANEL_2, LINE, 13)
    condition = weather.get("condition", "unknown")
    temperature = weather.get("temperature_c")
    _draw_weather_icon(screen, (rect.x + 29, rect.centery), condition)
    value = "--°C" if temperature is None else f"{temperature:.1f}°C"
    _text(screen, value, (rect.x + 55, rect.y + 10), 21, TEXT, True)
    label = "WEATHER" if condition in ("", "unknown") else condition.upper()
    _text(screen, label[:15], (rect.x + 56, rect.bottom - 11),
          9, MUTED, True, "bottomleft")


def _break_widget(screen, rect, break_time, break_delay):
    _panel(screen, rect, PANEL_2, LINE, 13)
    pygame.draw.circle(screen, AMBER, (rect.x + 18, rect.centery), 6)
    _text(screen, "NEXT BREAK", (rect.x + 31, rect.y + 10),
          9, MUTED, True)
    _text(screen, break_time, (rect.x + 31, rect.bottom - 10),
          20, AMBER, True, "bottomleft")
    _text(screen, break_delay, (rect.right - 12, rect.bottom - 12),
          10, MUTED, False, "bottomright")


def _utility_card(screen, rect, title, online, day_value, month_value, unit,
                  accent, icon):
    _panel(screen, rect, PANEL_2)
    icon_color = accent if online else MUTED
    if icon == "water":
        _draw_drop(screen, (rect.x + 25, rect.centery), icon_color)
    else:
        _draw_bolt(screen, (rect.x + 25, rect.centery), icon_color)
    _text(screen, title, (rect.x + 48, rect.centery), 14, TEXT, True, "midleft")
    _pill(screen, pygame.Rect(rect.x + 156, rect.centery - 12, 74, 24),
          "ONLINE" if online else "OFFLINE", active=online, fault=False)
    day = "--" if day_value is None else day_value
    month = "--" if month_value is None else month_value
    _text(screen, "DAY", (rect.right - 258, rect.y + 13), 10, MUTED, True)
    _text(screen, f"{day} {unit}", (rect.right - 258, rect.y + 31),
          18, accent if online else MUTED, True)
    _text(screen, "MONTH", (rect.right - 126, rect.y + 13), 10, MUTED, True)
    _text(screen, f"{month} {unit}", (rect.right - 126, rect.y + 31),
          18, accent if online else MUTED, True)




class TerminalSpaceReader:
    """Read Space directly from an interactive SSH terminal without Enter."""

    def __init__(self, stream=None):
        self.stream = stream or sys.stdin
        self.fd = None
        self.original_settings = None
        if termios is None or not self.stream.isatty():
            return
        try:
            self.fd = self.stream.fileno()
            self.original_settings = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)
            print("[DISPLAY] press Space in this terminal to save a screenshot", flush=True)
        except (AttributeError, OSError, termios.error):
            self.fd = None
            self.original_settings = None

    def space_pressed(self):
        if self.fd is None:
            return False
        pressed = False
        try:
            while select.select([self.stream], [], [], 0)[0]:
                char = self.stream.read(1)
                if not char:
                    break
                if char == " ":
                    pressed = True
        except (OSError, ValueError):
            return False
        return pressed

    def close(self):
        if self.fd is not None and self.original_settings is not None:
            try:
                termios.tcsetattr(self.fd, termios.TCSADRAIN, self.original_settings)
            except (OSError, termios.error):
                pass
            self.fd = None

def _save_screenshot(screen, output_dir, now=None):
    """Save the current dashboard surface as a timestamped PNG."""
    output_dir = Path(output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.datetime.now()).strftime("%Y%m%d_%H%M%S_%f")
    path = output_dir / f"dashboard_{timestamp}.png"
    pygame.image.save(screen, str(path))
    return path.resolve()

def run_dashboard(config, devices, state, stop_event, screenshot_event=None):
    pygame.init()
    fullscreen = config.get("fullscreen", True)
    flags = pygame.FULLSCREEN if fullscreen else pygame.RESIZABLE
    screen = pygame.display.set_mode((0, 0) if fullscreen else (1280, 720), flags)
    pygame.display.set_caption("Cutting / Gutting — Production")
    pygame.mouse.set_visible(not fullscreen)
    print(f"[DISPLAY] driver={pygame.display.get_driver()} "
          f"resolution={screen.get_width()}x{screen.get_height()}", flush=True)

    clock = pygame.time.Clock()
    fps = int(config.get("fps", 15))
    rate_left = ProductivityMeter(config.get("productivity_window_s", 60))
    rate_right = ProductivityMeter(config.get("productivity_window_s", 60))
    screenshot_dir = config.get("screenshot_dir", "screenshots")
    screenshot_requested = False
    screenshot_notice = ""
    screenshot_notice_until = 0.0
    terminal_keys = TerminalSpaceReader()

    try:
        while not stop_event.is_set():
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    stop_event.set()
                elif event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_ESCAPE, pygame.K_q):
                        stop_event.set()
                    elif event.key == pygame.K_SPACE:
                        screenshot_requested = True

            if terminal_keys.space_pressed():
                screenshot_requested = True
            if screenshot_event is not None and screenshot_event.is_set():
                screenshot_event.clear()
                screenshot_requested = True

            snapshot = state.snapshot()
            w, h = screen.get_size()
            screen.fill(BG)
            margin, gap = 22, 12

            left = _device(snapshot, "vision_left")
            right = _device(snapshot, "vision_right")
            gut_left = _device(snapshot, "gutting_left")
            gut_right = _device(snapshot, "gutting_right")
            total_left = _adjusted_counter(
                snapshot, "vision_left", "fish_counter", _value(left, "fish_counter", 0)
            )
            total_right = _adjusted_counter(
                snapshot, "vision_right", "fish_counter", _value(right, "fish_counter", 0)
            )
            total = total_left + total_right
            bad_left = _adjusted_counter(
                snapshot, "vision_left", "ejected_fish", _value(left, "ejected_fish", 0)
            )
            bad_right = _adjusted_counter(
                snapshot, "vision_right", "ejected_fish", _value(right, "ejected_fish", 0)
            )
            bad = bad_left + bad_right
            good = max(0, total - bad)
            good_left = max(0, total_left - bad_left)
            good_right = max(0, total_right - bad_right)
            belly_left = _adjusted_counter(
                snapshot, "gutting_left", "ejector_count",
                _value(gut_left, "ejector_count", 0),
            )
            belly_right = _adjusted_counter(
                snapshot, "gutting_right", "ejector_count",
                _value(gut_right, "ejector_count", 0),
            )
            belly = belly_left + belly_right
            fish_min_left = rate_left.update(total_left)
            fish_min_right = rate_right.update(total_right)
            fish_min = fish_min_left + fish_min_right
            people = _people_count(snapshot, config)
            cadence_worker = fish_min / people if people is not None and people > 0 else None

            _text(screen, "PRODUCTION  •  CUTTING / GUTTING", (margin, 13), 20, TEXT, True)
            mqtt_ok = snapshot.get("rpi", {}).get("mqtt_connected", False)
            _pill(screen, pygame.Rect(margin, 43, 105, 23),
                  "MQTT OK" if mqtt_ok else "MQTT OFF", active=mqtt_ok, fault=not mqtt_ok)
            _text(screen, time.strftime("%H:%M:%S"),
                  (w // 2, 4), 32, TEXT, True, "midtop")
            _text(screen, time.strftime("%d/%m/%Y"),
                  (w // 2, 45), 11, MUTED, False, "midtop")
            break_time, break_delay = _next_break(
                snapshot.get("rpi", {}).get("breaks", [])
            )
            weather_rect = pygame.Rect(w - margin - 162, 9, 162, 57)
            pause_w = min(250, max(205, int(w * 0.19)))
            pause_rect = pygame.Rect(weather_rect.x - gap - pause_w, 9, pause_w, 57)
            _break_widget(screen, pause_rect, break_time, break_delay)
            _weather_widget(screen, weather_rect, snapshot.get("weather", {}))

            trips = []
            offline = []
            for name, label in (("gutting_left", "Left gutting"),
                                ("gutting_right", "Right gutting")):
                item = _device(snapshot, name)
                if bool(_value(item, "motors_trip", 0)):
                    trips.append(label)
                if not item.get("connected"):
                    offline.append(label)
            gpio_state = snapshot.get("rpi", {}).get("gpio", {})
            if bool(gpio_state.get("cutting_motors_trip")):
                trips.append("Cutting machine")
            banner = pygame.Rect(margin, 77, w - 2 * margin, 52)
            _alarm_banner(screen, banner, trips, offline)

            kpi_y, kpi_h = 141, 150
            available = w - 2 * margin - 5 * gap
            widths = [int(available * 0.24), int(available * 0.18),
                      int(available * 0.13)]
            remaining = available - sum(widths)
            widths += [remaining // 3, remaining // 3, remaining - 2 * (remaining // 3)]
            titles = (("Productivity", f"{fish_min:.1f}", "fish / minute", BLUE, True),
                      ("Daily total", total, "fish today", CYAN, False),
                      ("People", people, "", CYAN, False),
                      ("Good", good, "good fish", GREEN, False),
                      ("Bad", bad, "vision ejections", RED if bad else AMBER, False),
                      ("Belly", belly, "orientation ejections", AMBER, False))
            x = margin
            for width, item in zip(widths, titles):
                rect = pygame.Rect(x, kpi_y, width, kpi_h)
                if item[0] == "People":
                    _people_kpi(screen, rect, people, cadence_worker)
                elif item[0] in ("Good", "Bad", "Belly"):
                    _quality_kpi(screen, rect, item[0], item[1], total, item[3])
                else:
                    _kpi(screen, rect, *item)
                x += width + gap

            machine_y = kpi_y + kpi_h + gap
            utility_h = 62
            utility_y = h - margin - utility_h
            cip_h = 48
            cip_y = utility_y - gap - cip_h
            cip_title_y = cip_y - 22
            cutting_h = 58
            cutting_y = cip_title_y - gap - cutting_h
            machine_h = max(145, cutting_y - gap - machine_y)
            machine_w = (w - 2 * margin - gap) // 2
            _machine_card(screen, pygame.Rect(margin, machine_y, machine_w, machine_h),
                          devices.get("gutting_left", {}).get("label", "Left gutting"),
                          gut_left, fish_min_left, rate_left.points(),
                          {"total": total_left, "good": good_left,
                           "bad": bad_left, "belly": belly_left})
            _machine_card(screen, pygame.Rect(margin + machine_w + gap, machine_y,
                                               w - 2 * margin - gap - machine_w, machine_h),
                          devices.get("gutting_right", {}).get("label", "Right gutting"),
                          gut_right, fish_min_right, rate_right.points(),
                          {"total": total_right, "good": good_right,
                           "bad": bad_right, "belly": belly_right})

            _cutting_status(
                screen,
                pygame.Rect(margin, cutting_y, w - 2 * margin, cutting_h),
                gpio_state,
                snapshot.get("rpi", {}).get("gpio_error", ""),
            )
            _text(screen, "CIP — RASPBERRY PI OUTPUTS / CP-IO22", (margin, cip_title_y),
                  13, MUTED, True)
            cip_w = (w - 2 * margin - 2 * gap) // 3
            cip_data = snapshot.get("rpi", {}).get("cip", {})
            cip_items = (("Left gutting", "gutting_left"),
                         ("Cutting machine", "cutting"),
                         ("Right gutting", "gutting_right"))
            for index, (label, name) in enumerate(cip_items):
                x = margin + index * (cip_w + gap)
                width = cip_w if index < 2 else w - margin - x
                _cip_card(screen, pygame.Rect(x, cip_y, width, cip_h),
                          label, cip_data.get(name, {}))

            utility_w = (w - 2 * margin - gap) // 2
            electricity = _device(snapshot, "electricity_meter")
            water = _device(snapshot, "water_meter")
            _utility_card(
                screen, pygame.Rect(margin, utility_y, utility_w, utility_h),
                "ELECTRICITY", bool(electricity.get("connected")),
                _value(electricity, "energy_today_kwh", None),
                _value(electricity, "energy_month_kwh", None), "kWh", AMBER,
                "electricity",
            )
            _utility_card(
                screen, pygame.Rect(margin + utility_w + gap, utility_y,
                                    w - 2 * margin - gap - utility_w, utility_h),
                "WATER", bool(water.get("connected")),
                _value(water, "water_today_m3", None),
                _value(water, "water_month_m3", None), "m³", CYAN, "water",
            )

            if screenshot_requested:
                try:
                    screenshot_path = _save_screenshot(screen, screenshot_dir)
                    screenshot_notice = f"Screenshot saved: {screenshot_path.name}"
                    print(f"[DISPLAY] screenshot saved: {screenshot_path}", flush=True)
                except Exception as error:
                    screenshot_notice = f"Screenshot failed: {error}"
                    print(f"[DISPLAY] screenshot failed: {error}", flush=True)
                screenshot_notice_until = time.monotonic() + 3.0
                screenshot_requested = False

            if screenshot_notice and time.monotonic() < screenshot_notice_until:
                notice_image = _font(14, True).render(screenshot_notice, True, TEXT)
                notice_rect = notice_image.get_rect()
                notice_rect.bottomright = (w - margin - 13, h - margin - 13)
                background = notice_rect.inflate(24, 16)
                pygame.draw.rect(screen, (23, 58, 94), background, border_radius=9)
                pygame.draw.rect(screen, BLUE, background, 1, border_radius=9)
                screen.blit(notice_image, notice_rect)

            pygame.display.flip()
            clock.tick(fps)
    finally:
        terminal_keys.close()
        pygame.quit()
