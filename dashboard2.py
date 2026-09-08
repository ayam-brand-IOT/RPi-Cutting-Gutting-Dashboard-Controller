"""System overview with a mirrored LEFT / RIGHT production layout.

Launch the live system: python main.py --dashboard 2
The original dashboard remains available with --dashboard 1 (the default).
"""
from __future__ import annotations

import time
import pygame
from dashboard import (
    BG, PANEL, PANEL_2, LINE, TEXT, MUTED, GREEN, BLUE, CYAN, AMBER, RED,
    ProductivityMeter, TerminalSpaceReader, _font, _text, _panel,
    _device, _value, _adjusted_counter, _people_count, _next_break,
    _weather_widget, _break_widget, _status_light,
    _cip_card, _save_screenshot,
)


def _number(value):
    return f"{value:,}".replace(",", " ")


def _side(snapshot, name, meter):
    vision = _device(snapshot, "vision_" + name)
    gutting = _device(snapshot, "gutting_" + name)
    total = _adjusted_counter(snapshot, "vision_" + name, "fish_counter",
                              _value(vision, "fish_counter", 0))
    bad = _adjusted_counter(snapshot, "vision_" + name, "ejected_fish",
                            _value(vision, "ejected_fish", 0))
    belly = _adjusted_counter(snapshot, "gutting_" + name, "ejector_count",
                              _value(gutting, "ejector_count", 0))
    return dict(total=total, good=max(0, total - bad), bad=bad, belly=belly,
                rate=meter.update(total), history=meter.points(), device=gutting)


def _summary(screen, rect, label, value, detail, accent):
    _panel(screen, rect, PANEL, LINE, 12)
    pygame.draw.rect(screen, accent, (rect.x + 14, rect.y + 12, 3, 16), border_radius=1)
    _text(screen, label, (rect.x + 25, rect.y + 10), 14, MUTED, True)
    _text(screen, value, (rect.x + 15, rect.y + 29), 28, accent, True,
          max_width=rect.w - 30)
    _text(screen, detail, (rect.x + 15, rect.bottom - 5), 14, MUTED,
          anchor="bottomleft", max_width=rect.w - 30)


def _connection(screen, x, y, label, connected):
    accent = GREEN if connected else AMBER
    pygame.draw.circle(screen, accent, (x, y), 4)
    _text(screen, label, (x + 12, y), 14, TEXT, anchor="midleft")
    _text(screen, "OK" if connected else "OFF", (x + 148, y), 14,
          accent, anchor="midright")


def _utilities(screen, snapshot):
    rect = pygame.Rect(882, 474, 382, 140)
    _panel(screen, rect)
    _text(screen, "UTILITIES", (898, 486), 15, MUTED, True)
    _text(screen, "TODAY", (1087, 492), 14, MUTED, anchor="center")
    _text(screen, "MONTH", (1198, 492), 14, MUTED, anchor="center")
    for y, name, label, day_key, month_key, unit, accent in (
        (536, "electricity_meter", "Electricity", "energy_today_kwh", "energy_month_kwh", "kWh", AMBER),
        (587, "water_meter", "Water", "water_today_m3", "water_month_m3", "m³", CYAN),
    ):
        device = _device(snapshot, name)
        online = bool(device.get("connected"))
        pygame.draw.circle(screen, GREEN if online else AMBER, (901, y - 4), 4)
        _text(screen, label, (913, y - 5), 16, TEXT, True, "midleft")
        _text(screen, "ONLINE" if online else "OFFLINE", (913, y + 13), 14, MUTED, anchor="midleft")
        for x, key in ((1087, day_key), (1198, month_key)):
            value = _value(device, key, None)
            value = "--" if value is None else str(value)
            _text(screen, value, (x, y - 5), 21, accent if online else MUTED,
                  True, "center", max_width=102)
            _text(screen, unit, (x, y + 15), 14, MUTED, anchor="center")


def draw_dashboard(screen, snapshot, config, devices, rate_left, rate_right):
    """Draw one frame; no device I/O or output writes."""
    screen.fill(BG)
    left = _side(snapshot, "left", rate_left)
    right = _side(snapshot, "right", rate_right)
    rpi = snapshot.get("rpi", {})
    gpio = rpi.get("gpio", {})
    people = _people_count(snapshot, config)
    total = left["total"] + right["total"]
    rate = left["rate"] + right["rate"]
    cadence = rate / people if people is not None and people > 0 else None
    _text(screen, "LINE OVERVIEW", (20, 13), 24, TEXT, True)
    _text(screen, "CUTTING / GUTTING", (21, 45), 14, MUTED)
    _text(screen, time.strftime("%H:%M:%S"), (605, 8), 34, TEXT, True, "midtop")
    _text(screen, time.strftime("%d / %m / %Y"), (605, 49), 14, MUTED, anchor="midtop")
    break_time, delay = _next_break(rpi.get("breaks", []))
    _break_widget(screen, pygame.Rect(827, 10, 252, 58), break_time, delay)
    _weather_widget(screen, pygame.Rect(1091, 10, 173, 58), snapshot.get("weather", {}))

    summaries = [
        ("TOTAL FISH / DAY", _number(total), "left + right", CYAN),
        ("PRODUCTIVITY", f"{rate:.1f}", "fish / min", BLUE),
        ("PEOPLE", "--" if people is None else str(people),
         "-- fish/min/worker" if cadence is None else f"{cadence:.1f} fish/min/worker", TEXT),
    ]
    for key, label, accent in (("good", "GOOD", GREEN), ("bad", "BAD / VISION", RED), ("belly", "BELLY / EJECTED", AMBER)):
        count = left[key] + right[key]
        percentage = 100 * count / total if total else 0
        summaries.append((label, f"{percentage:.1f}%", f"{_number(count)} fish", accent))
    for index, summary in enumerate(summaries):
        _summary(screen, pygame.Rect(16 + index * 210, 82, 198, 86), *summary)

    # Shared labels create a direct comparison without a table grid.
    _panel(screen, pygame.Rect(16, 180, 850, 434))
    for x, name, default in ((170, "gutting_left", "LEFT"), (712, "gutting_right", "RIGHT")):
        label = devices.get(name, {}).get("label", default).upper()
        _text(screen, label, (x, 205), 20, TEXT, True, "center", max_width=274)
    _text(screen, "PRODUCTION", (441, 205), 14, MUTED, True, "center")
    for y, label, unit in ((249, "DAILY TOTAL", "fish"), (302, "PRODUCTIVITY", "fish / min")):
        _text(screen, label, (441, y - 7), 15, MUTED, True, "center")
        _text(screen, unit, (441, y + 13), 14, MUTED, anchor="center")
    for x, item in ((170, left), (712, right)):
        _text(screen, _number(item["total"]), (x, 249), 34, CYAN, True, "center", max_width=270)
        _text(screen, f"{item['rate']:.1f}", (x, 302), 38, BLUE, True, "center", max_width=270)
    histories = left["history"] + right["history"]
    ceiling = max(1.0, max(histories, default=1.0) * 1.12)
    _text(screen, "SPEED TRACK", (441, 354), 14, MUTED, True, "center")
    _text(screen, f"0–{ceiling:.0f} fish/min", (441, 377), 14, MUTED, anchor="center")
    for x, item in ((36, left), (578, right)):
        _trend(screen, pygame.Rect(x, 334, 268, 65), item["history"], ceiling)
    for y, key, label, accent in ((433, "good", "GOOD", GREEN), (477, "bad", "BAD / VISION", RED), (521, "belly", "BELLY EJECTIONS", AMBER)):
        _text(screen, label, (441, y), 15, MUTED, True, "center")
        for x, item in ((170, left), (712, right)):
            count = item[key]
            percentage = 100 * count / item["total"] if item["total"] else 0
            _text(screen, f"{percentage:.1f}%", (x - 34, y), 28, accent, True, "center")
            _text(screen, f"{_number(count)} fish", (x + 57, y), 14, MUTED, anchor="midleft", max_width=90)
    for x, item in ((36, left), (578, right)):
        device = item["device"]
        values = device.get("values", {})
        online = bool(device.get("connected"))
        _status_light(screen, (x + 8, 564), "MOTORS", bool(values.get("motors_on")), online, bool(values.get("motors_trip")))
        _status_light(screen, (x + 160, 564), "BELT", bool(values.get("belt_on")), online)
        _text(screen, "RPM  {} / {} / {}".format(values.get("rpm_blade", 0), values.get("rpm_wheel1", 0), values.get("rpm_wheel2", 0)),
              (x + 134, 592), 14, MUTED, anchor="center", max_width=268)

    _panel(screen, pygame.Rect(882, 180, 382, 200))
    trips = [label for label, item in (("LEFT", left), ("RIGHT", right)) if _value(item["device"], "motors_trip", 0)]
    if gpio.get("cutting_motors_trip"):
        trips.append("CUTTING")
    names = ("vision_left", "vision_right", "gutting_left", "gutting_right", "electricity_meter", "water_meter")
    degraded = (not rpi.get("mqtt_connected") or any(not _device(snapshot, n).get("connected") for n in names)
                or bool(rpi.get("gpio_error")) or not all(k in gpio for k in ("cutting_motors_on", "cutting_motors_trip")))
    status, accent = ("MOTOR TRIP", RED) if trips else (("CHECK SYSTEM", AMBER) if degraded else ("SYSTEM READY", GREEN))
    pygame.draw.circle(screen, accent, (906, 205), 6)
    _text(screen, status, (924, 205), 20, accent, True, "midleft")
    _text(screen, " / ".join(trips) if trips else "CONNECTIONS & CONTROLLER", (901, 232), 14, MUTED)
    for x, y, label, connected in (
        (902, 268, "Vision left", _device(snapshot, "vision_left").get("connected")),
        (1080, 268, "Vision right", _device(snapshot, "vision_right").get("connected")),
        (902, 302, "Gutting left", left["device"].get("connected")),
        (1080, 302, "Gutting right", right["device"].get("connected")),
        (902, 346, "MQTT", rpi.get("mqtt_connected")),
        (1080, 346, "GPIO", not rpi.get("gpio_error") and all(k in gpio for k in ("cutting_motors_on", "cutting_motors_trip"))),
    ):
        _connection(screen, x, y, label, connected)
    _panel(screen, pygame.Rect(882, 392, 382, 70))
    _text(screen, "CUTTING MACHINE", (900, 402), 15, TEXT, True)
    available = not rpi.get("gpio_error") and all(k in gpio for k in ("cutting_motors_on", "cutting_motors_trip"))
    _status_light(screen, (906, 442), "MOTORS", bool(gpio.get("cutting_motors_on")), available, bool(gpio.get("cutting_motors_trip")))
    safety = "GPIO OFFLINE" if rpi.get("gpio_error") else ("NOT CONFIG" if not available else ("TRIP ACTIVE" if gpio.get("cutting_motors_trip") else "TRIP OK"))
    _text(screen, safety, (1246, 442), 14, RED if gpio.get("cutting_motors_trip") else MUTED, anchor="midright")
    _utilities(screen, snapshot)
    cip = rpi.get("cip", {})
    for index, (name, label) in enumerate((("gutting_left", "CIP / LEFT"), ("cutting", "CIP / CUTTING"), ("gutting_right", "CIP / RIGHT"))):
        _cip_card(screen, pygame.Rect(16 + index * 420, 628, 408, 76), label, cip.get(name, {}))


def _trend(screen, rect, history, ceiling):
    # Both sides share a scale so the visual comparison reflects actual throughput.
    pygame.draw.rect(screen, PANEL_2, rect, border_radius=8)
    pygame.draw.line(screen, LINE, (rect.x + 5, rect.centery), (rect.right - 5, rect.centery))
    if len(history) < 2:
        _text(screen, "Collecting…", rect.center, 14, MUTED, anchor="center")
        return
    points = [(rect.x + 6 + round((rect.w - 12) * i / (len(history) - 1)),
               rect.bottom - 6 - round((rect.h - 12) * value / ceiling)) for i, value in enumerate(history)]
    pygame.draw.polygon(screen, (17, 45, 68), [(points[0][0], rect.bottom - 5), *points, (points[-1][0], rect.bottom - 5)])
    pygame.draw.lines(screen, BLUE, False, points, 2)
    pygame.draw.circle(screen, CYAN, points[-1], 3)


def run_dashboard(config, devices, state, stop_event, screenshot_event=None):
    _font.cache_clear()
    pygame.init()
    fullscreen = config.get("fullscreen", True)
    flags = pygame.FULLSCREEN if fullscreen else pygame.RESIZABLE
    display = pygame.display.set_mode((0, 0) if fullscreen else (1280, 720), flags)
    # Keep the same readable proportions on HD and Full HD 13-inch displays.
    screen = pygame.Surface((1280, 720))
    pygame.display.set_caption("Cutting / Gutting — System Overview")
    pygame.mouse.set_visible(not fullscreen)
    print(f"[DISPLAY] driver={pygame.display.get_driver()} "
          f"resolution={display.get_width()}x{display.get_height()}", flush=True)

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

            w, h = screen.get_size()
            draw_dashboard(screen, state.snapshot(), config, devices, rate_left, rate_right)
            margin = 16

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

            scale = min(display.get_width() / w, display.get_height() / h)
            output_size = (round(w * scale), round(h * scale))
            display.fill(BG)
            output = (screen if output_size == screen.get_size() else
                      pygame.transform.smoothscale(screen, output_size))
            display.blit(output, output.get_rect(center=display.get_rect().center))
            pygame.display.flip()
            clock.tick(fps)
    finally:
        terminal_keys.close()
        pygame.quit()
