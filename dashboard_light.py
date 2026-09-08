"""Minimal production dashboard with a dark theme. Launch: python main.py --dashboard light."""
from __future__ import annotations

import time
import pygame
from dashboard import (ProductivityMeter, TerminalSpaceReader, _font,
                       _device, _value, _people_count, _next_break, _save_screenshot)
from dashboard2 import _side, _number

BG = (7, 13, 23)
PANEL = (16, 27, 43)
TEXT = (239, 245, 251)
MUTED = (168, 184, 203)
LINE = (39, 56, 77)
BLUE = (68, 158, 255)
GREEN = (43, 210, 145)
AMBER = (255, 181, 68)
RED = (247, 100, 111)
WHITE = (255, 255, 255)
TEAL = (57, 211, 221)
PURPLE = (185, 151, 247)
QUALITY_COLORS = {'good': GREEN, 'bad': RED, 'belly': AMBER}
QUALITY_TINTS = {'good': (17, 48, 42), 'bad': (53, 29, 40),
                 'belly': (49, 40, 29)}



def _label(screen, text, pos, size=16, color=TEXT, bold=False,
           anchor="topleft", width=None):
    text = str(text)
    while width is not None and size > 14 and _font(size, bold).size(text)[0] > width:
        size -= 1
    rendered = _font(size, bold).render(text, True, color)
    rect = rendered.get_rect(**{anchor: pos})
    screen.blit(rendered, rect)
    return rect


def _status(screen, x, y, text, color):
    pygame.draw.circle(screen, color, (x, y), 4)
    _label(screen, text, (x + 12, y), 14, color, anchor="midleft")


def _motor(screen, x, y, label, active, online, trip=False):
    state = "TRIP" if trip else ("ON" if active else "OFF") if online else "--"
    color = RED if trip else (GREEN if active else MUTED) if online else AMBER
    _status(screen, x, y, f"{label} {state}", color)


def _percent(item, key):
    return f"{100 * item[key] / item['total'] if item['total'] else 0:.1f}%"


def _trend(screen, rect, history, ceiling):
    pygame.draw.line(screen, LINE, (rect.x, rect.bottom), (rect.right, rect.bottom))
    if len(history) < 2:
        _label(screen, "Collecting speed history…", rect.center, 14, MUTED, anchor="center")
        return
    points = [(rect.x + round(i * rect.w / (len(history) - 1)),
               rect.bottom - round(v / ceiling * rect.h)) for i, v in enumerate(history)]
    pygame.draw.lines(screen, BLUE, False, points, 2)
    pygame.draw.circle(screen, BLUE, points[-1], 3)


def _production(screen, x, item, title, vision_online, ceiling):
    pygame.draw.rect(screen, PANEL, (x, 202, 604, 278), border_radius=12)
    _label(screen, title, (x + 20, 215), 22, TEXT, True, width=265)
    online = bool(item['device'].get('connected'))
    _status(screen, x + 341, 230, "Vision " + ("OK" if vision_online else "OFF"), GREEN if vision_online else AMBER)
    _status(screen, x + 472, 230, "Control " + ("OK" if online else "OFF"), GREEN if online else AMBER)
    _label(screen, f"{item['rate']:.1f}", (x + 20, 244), 52, BLUE, True, width=240)
    _label(screen, "fish / min", (x + 24, 307), 15, MUTED)
    _label(screen, _number(item['total']), (x + 326, 251), 38, TEAL, True, width=252)
    _label(screen, "fish today", (x + 329, 307), 15, MUTED)
    for offset, key, label in ((24, 'good', 'Good'), (216, 'bad', 'Bad'), (408, 'belly', 'Belly')):
        px = x + offset
        pygame.draw.rect(screen, QUALITY_TINTS[key], (px - 8, 334, 180, 53), border_radius=7)
        _label(screen, label, (px, 343), 15, QUALITY_COLORS[key])
        _label(screen, _percent(item, key), (px + 61, 339), 23, QUALITY_COLORS[key], True, width=105)
        _label(screen, _number(item[key]) + " fish", (px + 61, 367), 14, MUTED)
    _trend(screen, pygame.Rect(x + 24, 398, 556, 40), item['history'], ceiling)
    values = item['device'].get('values', {})
    _motor(screen, x + 25, 460, 'Motors', bool(values.get('motors_on')), online, bool(values.get('motors_trip')))
    _motor(screen, x + 164, 460, 'Belt', bool(values.get('belt_on')), online)
    _label(screen, 'RPM  {} / {} / {}'.format(values.get('rpm_blade', 0), values.get('rpm_wheel1', 0), values.get('rpm_wheel2', 0)),
           (x + 580, 460), 14, MUTED, anchor='midright', width=265)


def draw_dashboard(screen, snapshot, config, devices, rate_left, rate_right):
    screen.fill(BG)
    left, right = _side(snapshot, 'left', rate_left), _side(snapshot, 'right', rate_right)
    rpi = snapshot.get('rpi', {})
    gpio = rpi.get('gpio', {})
    gpio_ready = not rpi.get('gpio_error') and all(k in gpio for k in ('cutting_motors_on', 'cutting_motors_trip'))
    trips = [label for label, item in (('Left', left), ('Right', right)) if _value(item['device'], 'motors_trip', 0)]
    if gpio.get('cutting_motors_trip'): trips.append('Cutting')
    names = ('vision_left', 'vision_right', 'gutting_left', 'gutting_right', 'water_meter', 'electricity_meter')
    degraded = not rpi.get('mqtt_connected') or not gpio_ready or any(not _device(snapshot, n).get('connected') for n in names)
    status, color = ("Motor trip: " + ' / '.join(trips), RED) if trips else (("Check system connections", AMBER) if degraded else ("System ready", GREEN))
    _label(screen, 'Production', (24, 17), 28, TEXT, True)
    _status(screen, 28, 62, status, color)
    pause, delay = _next_break(rpi.get('breaks', []))
    _label(screen, 'Next break', (530, 21), 14, MUTED)
    _label(screen, pause, (530, 43), 23, AMBER, True)
    _label(screen, delay, (623, 53), 14, MUTED, anchor='midleft', width=170)
    weather = snapshot.get('weather', {})
    temp = weather.get('temperature_c')
    _label(screen, '--°C' if temp is None else f"{temp:.1f}°C", (866, 18), 27, TEXT)
    _label(screen, str(weather.get('condition') or 'Weather unknown')[:19], (866, 53), 14, MUTED)
    _label(screen, time.strftime('%H:%M:%S'), (1254, 15), 32, TEXT, True, 'topright')
    _label(screen, time.strftime('%d / %m / %Y'), (1254, 56), 14, MUTED, anchor='topright')
    pygame.draw.line(screen, LINE, (24, 91), (1256, 91))

    total = {k: left[k] + right[k] for k in ('total', 'good', 'bad', 'belly')}
    rate = left['rate'] + right['rate']
    people = _people_count(snapshot, config)
    cadence = rate / people if people is not None and people > 0 else None
    summary = [('Daily total', _number(total['total']), 'fish', TEAL),
               ('Productivity', f"{rate:.1f}", 'fish / min', BLUE),
               ('People', '--' if people is None else people,
                '-- fish/min/worker' if cadence is None else f"{cadence:.1f} fish/min/worker", PURPLE)]
    for key, label in (('good', 'Good'), ('bad', 'Bad / vision'), ('belly', 'Belly / ejected')):
        summary.append((label, _percent(total, key), _number(total[key]) + ' fish', QUALITY_COLORS[key]))
    for i, (label, value, detail, accent) in enumerate(summary):
        x = 24 + i * 208
        pygame.draw.rect(screen, accent, (x, 97, 30, 3), border_radius=1)
        _label(screen, label, (x, 105), 15, accent)
        _label(screen, value, (x, 127), 32, accent, True, width=184)
        _label(screen, detail, (x, 169), 14, MUTED, width=184)
    histories = left['history'] + right['history']
    ceiling = max(1.0, max(histories, default=1) * 1.12)
    for x, name, item in ((24, 'left', left), (652, 'right', right)):
        title = devices.get('gutting_' + name, {}).get('label', name.upper())
        _production(screen, x, item, title, bool(_device(snapshot, 'vision_' + name).get('connected')), ceiling)

    # Only three quiet sections for the secondary information.
    for x, label, accent in ((24, 'SYSTEM', BLUE), (444, 'CLEANING / CIP', PURPLE), (864, 'UTILITIES', TEAL)):
        _label(screen, label, (x, 512), 14, accent, True)
        pygame.draw.line(screen, LINE, (x, 537), (x + 392, 537))
        pygame.draw.line(screen, accent, (x, 537), (x + 38, 537), 2)
    _status(screen, 28, 563, 'MQTT ' + ('OK' if rpi.get('mqtt_connected') else 'OFFLINE'), GREEN if rpi.get('mqtt_connected') else AMBER)
    _status(screen, 221, 563, 'GPIO ' + ('OK' if gpio_ready else ('OFFLINE' if rpi.get('gpio_error') else 'NOT CONFIG')), GREEN if gpio_ready else AMBER)
    _label(screen, 'Cutting machine', (24, 597), 17, TEXT, True)
    _motor(screen, 28, 638, 'Motors', bool(gpio.get('cutting_motors_on')), gpio_ready, bool(gpio.get('cutting_motors_trip')))
    safety = 'TRIP ACTIVE' if gpio.get('cutting_motors_trip') else ('Trip OK' if gpio_ready else 'Trip --')
    _label(screen, safety, (222, 638), 15, RED if gpio.get('cutting_motors_trip') else MUTED, anchor='midleft')
    _label(screen, 'RPM: blade / wheel 1 / wheel 2', (24, 681), 14, MUTED)
    cip = rpi.get('cip', {})
    for y, name, label in ((563, 'gutting_left', 'Left'), (613, 'cutting', 'Cutting'), (663, 'gutting_right', 'Right')):
        item = cip.get(name, {})
        active, enabled = bool(item.get('output')), bool(item.get('enable'))
        _label(screen, label, (444, y), 17, TEXT, anchor='midleft')
        _status(screen, 531, y, 'ACTIVE' if active else ('READY' if enabled else 'OFF'), GREEN if active else (PURPLE if enabled else MUTED))
        _label(screen, f"ON {item.get('on_ms', '--')} / OFF {item.get('off_ms', '--')} ms", (836, y), 14, MUTED, anchor='midright', width=206)
    for y, name, label, keys, unit in ((563, 'electricity_meter', 'Electricity', ('energy_today_kwh', 'energy_month_kwh'), 'kWh'), (637, 'water_meter', 'Water', ('water_today_m3', 'water_month_m3'), 'm³')):
        device = _device(snapshot, name)
        online = bool(device.get('connected'))
        accent = AMBER if name == 'electricity_meter' else BLUE
        _label(screen, label, (864, y), 17, accent, True, 'midleft')
        _status(screen, 1154, y, 'ONLINE' if online else 'OFFLINE', GREEN if online else AMBER)
        for x, key, period in ((864, keys[0], 'Day'), (1070, keys[1], 'Month')):
            value = _value(device, key, None)
            _label(screen, f"{period}  {'--' if value is None else value} {unit}", (x, y + 32), 17, accent if online else MUTED, anchor='midleft', width=190)


def run_dashboard(config, devices, state, stop_event, screenshot_event=None):
    _font.cache_clear()
    pygame.init()
    fullscreen = config.get("fullscreen", True)
    flags = pygame.FULLSCREEN if fullscreen else pygame.RESIZABLE
    display = pygame.display.set_mode((0, 0) if fullscreen else (1280, 720), flags)
    # Keep the same readable proportions on HD and Full HD 13-inch displays.
    screen = pygame.Surface((1280, 720))
    pygame.display.set_caption("Cutting / Gutting — Light")
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
                notice_image = _font(14, True).render(screenshot_notice, True, WHITE)
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
