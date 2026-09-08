"""Two-page overview: statistics for 20 seconds, system for 10 seconds.

Run with python main.py --dashboard cycle. Space saves the visible page.
"""
from __future__ import annotations

import math
import time
import pygame
from dashboard import (
    BG, PANEL, PANEL_2, LINE, TEXT, MUTED, GREEN, BLUE, CYAN, AMBER, RED,
    ProductivityMeter, TerminalSpaceReader, _font, _text, _panel,
    _device, _value, _people_count, _next_break, _weather_widget,
    _break_widget, _status_light, _save_screenshot,
)
from dashboard2 import _side, _number, _summary, _trend

STATS_SECONDS = 20.0
SYSTEM_SECONDS = 10.0


def page_at(elapsed):
    """Monotonic elapsed time, measured from the start of this display session."""
    phase = max(0.0, elapsed) % (STATS_SECONDS + SYSTEM_SECONDS)
    if phase < STATS_SECONDS:
        return 'statistics', STATS_SECONDS - phase
    return 'system', STATS_SECONDS + SYSTEM_SECONDS - phase


def _header(screen, snapshot, page, remaining):
    _text(screen, 'PRODUCTION' if page == 'statistics' else 'SYSTEM OVERVIEW', (20, 13), 25, TEXT, True)
    _text(screen, 'CUTTING / GUTTING', (21, 46), 15, MUTED)
    _text(screen, time.strftime('%H:%M:%S'), (605, 8), 34, TEXT, True, 'midtop')
    _text(screen, time.strftime('%d / %m / %Y'), (605, 49), 14, MUTED, anchor='midtop')
    pause, delay = _next_break(snapshot.get('rpi', {}).get('breaks', []))
    _break_widget(screen, pygame.Rect(827, 10, 252, 58), pause, delay)
    _weather_widget(screen, pygame.Rect(1091, 10, 173, 58), snapshot.get('weather', {}))
    _text(screen, 'STATISTICS  20 s', (20, 699), 14, BLUE if page == 'statistics' else MUTED, page == 'statistics', 'midleft')
    _text(screen, 'SYSTEM  10 s', (190, 699), 14, BLUE if page == 'system' else MUTED, page == 'system', 'midleft')
    _text(screen, f"Next page in {math.ceil(remaining)} s", (1260, 699), 14, MUTED, anchor='midright')


def _statistics(screen, snapshot, config, devices, left, right):
    total = left['total'] + right['total']
    rate = left['rate'] + right['rate']
    people = _people_count(snapshot, config)
    cadence = rate / people if people is not None and people > 0 else None
    summaries = [('TOTAL FISH / DAY', _number(total), 'left + right', CYAN),
                 ('PRODUCTIVITY', f"{rate:.1f}", 'fish / min', BLUE),
                 ('PEOPLE', '--' if people is None else str(people),
                  '-- fish/min/worker' if cadence is None else f"{cadence:.1f} fish/min/worker", TEXT)]
    for key, label, accent in (('good', 'GOOD', GREEN), ('bad', 'BAD / VISION', RED), ('belly', 'BELLY / EJECTED', AMBER)):
        count = left[key] + right[key]
        summaries.append((label, f"{100 * count / total if total else 0:.1f}%", f"{_number(count)} fish", accent))
    for i, summary in enumerate(summaries):
        _summary(screen, pygame.Rect(16 + i * 210, 82, 198, 86), *summary)
    _panel(screen, pygame.Rect(16, 184, 1248, 490))
    for x, side in ((310, 'left'), (970, 'right')):
        title = devices.get('gutting_' + side, {}).get('label', side.upper())
        _text(screen, title.upper(), (x, 214), 25, TEXT, True, 'center', max_width=470)
    for y, label, unit in ((270, 'DAILY TOTAL', 'fish'), (339, 'PRODUCTIVITY', 'fish / min')):
        _text(screen, label, (640, y - 8), 17, MUTED, True, 'center')
        _text(screen, unit, (640, y + 17), 15, MUTED, anchor='center')
    for x, item in ((310, left), (970, right)):
        _text(screen, _number(item['total']), (x, 270), 44, CYAN, True, 'center', max_width=450)
        _text(screen, f"{item['rate']:.1f}", (x, 339), 54, BLUE, True, 'center', max_width=450)
    history = left['history'] + right['history']
    ceiling = max(1.0, max(history, default=1.0) * 1.12)
    _text(screen, 'SPEED TRACK', (640, 415), 16, MUTED, True, 'center')
    _text(screen, f"0–{ceiling:.0f} fish/min", (640, 443), 15, MUTED, anchor='center')
    for x, item in ((46, left), (706, right)):
        _trend(screen, pygame.Rect(x, 388, 528, 88), item['history'], ceiling)
    for y, key, label, accent in ((516, 'good', 'GOOD', GREEN), (576, 'bad', 'BAD / VISION', RED), (636, 'belly', 'BELLY EJECTIONS', AMBER)):
        _text(screen, label, (640, y), 17, MUTED, True, 'center')
        for x, item in ((310, left), (970, right)):
            count = item[key]
            _text(screen, f"{100 * count / item['total'] if item['total'] else 0:.1f}%", (x - 70, y), 36, accent, True, 'center')
            _text(screen, f"{_number(count)} fish", (x + 50, y), 19, MUTED, anchor='midleft', max_width=200)


def _connection(screen, label, online, position):
    _text(screen, label + (' ONLINE' if online else ' OFFLINE'), position, 16,
          GREEN if online else AMBER, True)


def _system(screen, snapshot, devices, left, right):
    rpi = snapshot.get('rpi', {})
    gpio = rpi.get('gpio', {})
    gpio_ready = not rpi.get('gpio_error') and all(k in gpio for k in ('cutting_motors_on', 'cutting_motors_trip'))
    trips = [label for label, item in (('LEFT', left), ('RIGHT', right)) if _value(item['device'], 'motors_trip', 0)]
    if gpio.get('cutting_motors_trip'): trips.append('CUTTING')
    names = ('vision_left', 'vision_right', 'gutting_left', 'gutting_right', 'electricity_meter', 'water_meter')
    degraded = not rpi.get('mqtt_connected') or not gpio_ready or any(not _device(snapshot, name).get('connected') for name in names)
    title, accent = ('MOTOR TRIP: ' + ' / '.join(trips), RED) if trips else (('CHECK SYSTEM CONNECTIONS', AMBER) if degraded else ('SYSTEM READY', GREEN))
    _panel(screen, pygame.Rect(16, 84, 1248, 62), PANEL, accent)
    _text(screen, title, (36, 115), 22, accent, True, 'midleft', max_width=800)
    _text(screen, 'MQTT ' + ('ONLINE' if rpi.get('mqtt_connected') else 'OFFLINE'), (1244, 115), 18,
          GREEN if rpi.get('mqtt_connected') else AMBER, True, 'midright')
    for x, side, item in ((16, 'left', left), (856, 'right', right)):
        _panel(screen, pygame.Rect(x, 160, 408, 236))
        _text(screen, devices.get('gutting_' + side, {}).get('label', side.upper()).upper(), (x + 20, 177), 23, TEXT, True, max_width=368)
        online = bool(item['device'].get('connected'))
        values = item['device'].get('values', {})
        _connection(screen, 'Control', online, (x + 20, 216))
        _connection(screen, 'Vision', bool(_device(snapshot, 'vision_' + side).get('connected')), (x + 213, 216))
        _status_light(screen, (x + 28, 267), 'MOTORS', bool(values.get('motors_on')), online, bool(values.get('motors_trip')))
        _status_light(screen, (x + 229, 267), 'BELT', bool(values.get('belt_on')), online)
        for i, (key, label) in enumerate((('rpm_blade', 'BLADE'), ('rpm_wheel1', 'WHEEL 1'), ('rpm_wheel2', 'WHEEL 2'))):
            cx = x + 68 + i * 136
            _text(screen, label, (cx, 315), 15, MUTED, True, 'center')
            _text(screen, str(values.get(key, 0)), (cx, 349), 30, TEXT, True, 'center', max_width=124)
        _text(screen, 'RPM', (x + 204, 381), 14, MUTED, anchor='center')
    _panel(screen, pygame.Rect(436, 160, 408, 236))
    _text(screen, 'CUTTING MACHINE', (456, 177), 23, TEXT, True)
    _text(screen, 'CP-IO22 / GPIO', (456, 219), 16, MUTED)
    _status_light(screen, (464, 267), 'MOTORS', bool(gpio.get('cutting_motors_on')), gpio_ready, bool(gpio.get('cutting_motors_trip')))
    safety = 'TRIP ACTIVE' if gpio.get('cutting_motors_trip') else ('TRIP OK' if gpio_ready else 'TRIP --')
    _text(screen, safety, (640, 323), 27, RED if gpio.get('cutting_motors_trip') else GREEN if gpio_ready else AMBER, True, 'center')
    _text(screen, 'GPIO OFFLINE' if rpi.get('gpio_error') else 'GPIO ONLINE' if gpio_ready else 'INPUTS NOT CONFIGURED', (640, 370), 16, MUTED, anchor='center')
    for x, name, label, keys, unit, accent in (
        (16, 'electricity_meter', 'ELECTRICITY', ('energy_today_kwh', 'energy_month_kwh'), 'kWh', AMBER),
        (646, 'water_meter', 'WATER', ('water_today_m3', 'water_month_m3'), 'm³', CYAN)):
        _panel(screen, pygame.Rect(x, 410, 618, 110))
        device = _device(snapshot, name)
        online = bool(device.get('connected'))
        _text(screen, label, (x + 20, 430), 19, accent, True)
        _text(screen, 'ONLINE' if online else 'OFFLINE', (x + 20, 472), 16, GREEN if online else AMBER)
        for cx, key, period in ((x + 310, keys[0], 'TODAY'), (x + 508, keys[1], 'MONTH')):
            value = _value(device, key, None)
            _text(screen, period, (cx, 435), 15, MUTED, True, 'center')
            _text(screen, f"{'--' if value is None else value} {unit}", (cx, 478), 26, accent if online else MUTED, True, 'center', max_width=185)
    cip = rpi.get('cip', {})
    for i, (name, label) in enumerate((('gutting_left', 'LEFT'), ('cutting', 'CUTTING'), ('gutting_right', 'RIGHT'))):
        x = 16 + i * 420
        item = cip.get(name, {})
        active, enabled = bool(item.get('output')), bool(item.get('enable'))
        _panel(screen, pygame.Rect(x, 534, 408, 140), PANEL, GREEN if active else LINE)
        _text(screen, 'CIP / ' + label, (x + 20, 551), 21, TEXT, True)
        _text(screen, 'ACTIVE' if active else 'READY' if enabled else 'OFF', (x + 388, 562), 18, GREEN if active else MUTED, True, 'midright')
        for cx, key, period in ((x + 104, 'on_ms', 'ON'), (x + 304, 'off_ms', 'OFF')):
            _text(screen, period, (cx, 603), 15, MUTED, True, 'center')
            _text(screen, f"{item.get(key, '--')} ms", (cx, 642), 26, TEXT, True, 'center', max_width=180)


def draw_dashboard(screen, snapshot, config, devices, rate_left, rate_right, elapsed=0):
    screen.fill(BG)
    # Update both meters on every frame, including during the system page.
    left = _side(snapshot, 'left', rate_left)
    right = _side(snapshot, 'right', rate_right)
    page, remaining = page_at(elapsed)
    _header(screen, snapshot, page, remaining)
    if page == 'statistics':
        _statistics(screen, snapshot, config, devices, left, right)
    else:
        _system(screen, snapshot, devices, left, right)


def run_dashboard(config, devices, state, stop_event, screenshot_event=None):
    _font.cache_clear()
    pygame.init()
    fullscreen = config.get("fullscreen", True)
    flags = pygame.FULLSCREEN if fullscreen else pygame.RESIZABLE
    display = pygame.display.set_mode((0, 0) if fullscreen else (1280, 720), flags)
    # Keep the same readable proportions on HD and Full HD 13-inch displays.
    screen = pygame.Surface((1280, 720))
    pygame.display.set_caption("Cutting / Gutting — Statistics / System")
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
    page_started_at = time.monotonic()

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
            draw_dashboard(screen, state.snapshot(), config, devices, rate_left, rate_right,
                           time.monotonic() - page_started_at)
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
