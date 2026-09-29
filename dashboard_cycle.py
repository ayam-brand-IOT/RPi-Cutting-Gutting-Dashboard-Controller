"""Two-page overview: Operation for 20 seconds, Maintenance for 10 seconds.

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
    _text(screen, 'OPERATION' if page == 'statistics' else 'MAINTENANCE', (20, 13), 25, TEXT, True)
    _text(screen, 'CUTTING / GUTTING', (21, 46), 15, MUTED)
    _text(screen, time.strftime('%H:%M:%S'), (605, 8), 34, TEXT, True, 'midtop')
    _text(screen, time.strftime('%d / %m / %Y'), (605, 49), 14, MUTED, anchor='midtop')
    pause, delay = _next_break(snapshot.get('rpi', {}).get('breaks', []))
    _break_widget(screen, pygame.Rect(827, 10, 252, 58), pause, delay)
    _weather_widget(screen, pygame.Rect(1091, 10, 173, 58), snapshot.get('weather', {}))
    _text(screen, 'OPERATION  20 s', (20, 699), 14, BLUE if page == 'statistics' else MUTED, page == 'statistics', 'midleft')
    _text(screen, 'MAINTENANCE  10 s', (190, 699), 14, BLUE if page == 'system' else MUTED, page == 'system', 'midleft')
    _text(screen, f"Next page in {math.ceil(remaining)} s", (1260, 699), 14, MUTED, anchor='midright')


def _machine_people(snapshot, side):
    """Use only an explicit side reading, never the global workforce count."""
    rpi = snapshot.get('rpi', {})
    if 'people_gpio' in rpi:
        return rpi['people_gpio'].get(side)
    per_machine = rpi.get('people_by_machine', {})
    value = per_machine.get(side)
    if value is None:
        for prefix in ('vision_', 'gutting_'):
            values = _device(snapshot, prefix + side).get('values', {})
            for key in ('people_count', 'persons_present', 'person_count', 'people_present'):
                if values.get(key) is not None:
                    value = values[key]
                    break
            if value is not None:
                break
    try:
        count = int(value)
        return count if not isinstance(value, bool) and count >= 0 and float(value) == count else None
    except (TypeError, ValueError, OverflowError):
        return None


def _combined_summary(screen, snapshot, config, left, right):
    total = left['total'] + right['total']
    rate = left['rate'] + right['rate']
    counts = [_machine_people(snapshot, side) for side in ('left', 'right')]
    people = sum(counts) if all(count is not None for count in counts) else _people_count(snapshot, config)
    cadence = rate / people if people is not None and people > 0 else None
    summaries = [('TOTAL FISH / DAY', _number(total), 'left + right', CYAN),
                 ('PRODUCTIVITY', f"{rate:.1f}", 'fish / min', BLUE),
                 ('PEOPLE', '--' if people is None else str(people),
                  '-- fish/min/worker' if cadence is None else f"{cadence:.1f} fish/min/worker", TEXT)]
    for key, label, accent in (('bad', 'REJECTED / VISION', RED), ('belly', 'WRONG SIDE', AMBER)):
        count = left[key] + right[key]
        summaries.append((label, f"{100 * count / total if total else 0:.1f}%", f"{_number(count)} fish", accent))
    for i, summary in enumerate(summaries):
        _summary(screen, pygame.Rect(16 + i * 252, 82, 240, 86), *summary)


def _belt_rpm(device):
    value = _value(device, 'rpm_belt', None)
    return '--' if not device.get('connected') or value is None else str(value)



def _productivity_direction(rate, history):
    """Compare with ~30 s ago (history samples every 2 s); ±10% is stable."""
    if len(history) < 16:
        return None
    previous = history[-16]
    delta = rate - previous
    threshold = previous * 0.10
    if math.isclose(abs(delta), threshold, rel_tol=1e-9, abs_tol=1e-9):
        return 0
    return 1 if delta > threshold else -1 if delta < -threshold else 0


def _productivity_indicator(screen, position, rate, history):
    direction = _productivity_direction(rate, history)
    x, y = position
    if direction in (None, 0):
        _text(screen, '--' if direction is None else '=', position, 32, MUTED,
              True, 'center')
    else:
        # Draw triangles directly so the symbol does not depend on font support.
        points = [(x, y - 12 * direction), (x - 13, y + 10 * direction),
                  (x + 13, y + 10 * direction)]
        pygame.draw.polygon(screen, GREEN if direction > 0 else RED, points)


def _productivity_alarm(meter, rate, target, now, reset_sequence=0, online=True):
    """Track continuous time below target, independently for each meter."""
    context = (target, reset_sequence)
    if getattr(meter, '_target_context', None) != context:
        meter._target_context = context
        meter._below_target_since = None
    if not online or not target or not math.isfinite(rate) or rate >= target:
        meter._below_target_since = None
        return False
    if meter._below_target_since is None:
        meter._below_target_since = now
    return now - meter._below_target_since > 60.0


def _statistics(screen, snapshot, config, devices, left, right):
    _panel(screen, pygame.Rect(16, 82, 1248, 592))
    for x, side in ((310, 'left'), (970, 'right')):
        title = devices.get('gutting_' + side, {}).get('label', side.upper())
        _text(screen, title.upper(), (x, 111), 25, TEXT, True, 'center', max_width=470)
    for y, label, unit in ((194, 'DAILY TOTAL', 'fish'), (280, 'PRODUCTIVITY', 'fish / min')):
        _text(screen, label, (640, y - 8), 17, MUTED, True, 'center')
        _text(screen, unit, (640, y + 17), 15, MUTED, anchor='center')
    for x, item in ((310, left), (970, right)):
        _text(screen, _number(item['total']), (x, 194), 44, CYAN, True, 'center', max_width=450)
        if item.get('productivity_visible', True):
            _text(screen, f"{item['rate']:.1f}", (x, 280), 54,
                  RED if item.get('productivity_alarm') else BLUE, True, 'center', max_width=280)
        _productivity_indicator(screen, (x - 175 if x < 640 else x + 175, 280),
                                item['rate'], item['history'])
    history = left['history'] + right['history']
    target = snapshot.get('rpi', {}).get('productivity_setpoint', 0)
    target = target if isinstance(target, (int, float)) and math.isfinite(target) and 0 < target <= 10000 else 0
    ceiling = max(1.0, max(max(history, default=1.0), target) * 1.12)
    _text(screen, 'SPEED TRACK', (640, 399), 16, MUTED, True, 'center')
    _text(screen, f"0–{ceiling:.0f} fish/min", (640, 429), 15, MUTED, anchor='center')
    for x, item in ((46, left), (706, right)):
        _trend(screen, pygame.Rect(x, 350, 528, 130), item['history'], ceiling)
        if target:
            y = 480 - 6 - round((130 - 12) * target / ceiling)
            pygame.draw.line(screen, AMBER, (x + 6, y), (x + 522, y), 2)
            _text(screen, f"TARGET {target:g} fish/min", (x + 528, 336), 16,
                  AMBER, True, 'midright')
    for y, key, label, accent in ((518, 'bad', 'REJECTED / VISION', RED), (576, 'belly', 'WRONG SIDE', AMBER)):
        _text(screen, label, (640, y), 17, MUTED, True, 'center')
        for x, item in ((310, left), (970, right)):
            count = item[key]
            _text(screen, f"{100 * count / item['total'] if item['total'] else 0:.1f}%", (x - 70, y), 36, accent, True, 'center')
            _text(screen, f"{_number(count)} fish", (x + 50, y), 19, MUTED, anchor='midleft', max_width=200)

    _text(screen, 'PEOPLE PRESENCE', (640, 620), 17, MUTED, True, 'center')
    for x, side in ((310, 'left'), (970, 'right')):
        people = _machine_people(snapshot, side)
        _text(screen, '--' if people is None else str(people), (x, 620),
              36, CYAN if people is not None else MUTED, True, 'center', max_width=160)

    # Cutting VFD source is not configured yet; do not substitute gutting belt RPM.
    _text(screen, 'FISH BELT SPEED   -- pockets/min', (640, 658), 17, MUTED,
          anchor='center')


def _connection(screen, label, online, position):
    _text(screen, label + (' ONLINE' if online else ' OFFLINE'), position, 16,
          GREEN if online else AMBER, True)


def _system(screen, snapshot, config, devices, left, right):
    _combined_summary(screen, snapshot, config, left, right)
    rpi = snapshot.get('rpi', {})
    gpio = rpi.get('gpio', {})
    gpio_ready = not rpi.get('gpio_error') and all(k in gpio for k in ('cutting_motors_on', 'cutting_motors_trip'))
    trips = [label for label, item in (('LEFT', left), ('RIGHT', right)) if _value(item['device'], 'motors_trip', 0)]
    if gpio.get('cutting_motors_trip'): trips.append('CUTTING')
    names = ('vision_left', 'vision_right', 'gutting_left', 'gutting_right', 'electricity_meter', 'water_meter')
    degraded = not rpi.get('mqtt_connected') or not gpio_ready or any(not _device(snapshot, name).get('connected') for name in names)
    title, accent = ('MOTOR TRIP: ' + ' / '.join(trips), RED) if trips else (('CHECK SYSTEM CONNECTIONS', AMBER) if degraded else ('SYSTEM READY', GREEN))
    _panel(screen, pygame.Rect(16, 180, 1248, 46), PANEL, accent)
    _text(screen, title, (36, 203), 22, accent, True, 'midleft', max_width=800)
    _text(screen, 'MQTT ' + ('ONLINE' if rpi.get('mqtt_connected') else 'OFFLINE'), (1244, 203), 18,
          GREEN if rpi.get('mqtt_connected') else AMBER, True, 'midright')
    for x, side, item in ((16, 'left', left), (856, 'right', right)):
        _panel(screen, pygame.Rect(x, 238, 408, 196))
        _text(screen, devices.get('gutting_' + side, {}).get('label', side.upper()).upper(), (x + 20, 251), 23, TEXT, True, max_width=368)
        online = bool(item['device'].get('connected'))
        values = item['device'].get('values', {})
        _connection(screen, 'Control', online, (x + 20, 289))
        _connection(screen, 'Vision', bool(_device(snapshot, 'vision_' + side).get('connected')), (x + 213, 289))
        _status_light(screen, (x + 28, 332), 'MOTORS', bool(values.get('motors_on')), online, bool(values.get('motors_trip')))
        _status_light(screen, (x + 229, 332), 'BELT', bool(values.get('belt_on')), online)
        for i, (key, label) in enumerate((('rpm_blade', 'BLADE'), ('rpm_wheel1', 'WHEEL 1'), ('rpm_wheel2', 'WHEEL 2'), ('rpm_belt', 'BELT'))):
            cx = x + 51 + i * 102
            _text(screen, label, (cx, 369), 15, MUTED, True, 'center')
            value = _belt_rpm(item['device']) if key == 'rpm_belt' else str(values.get(key, 0))
            _text(screen, value, (cx, 398), 28, TEXT, True, 'center', max_width=90)
        _text(screen, 'RPM', (x + 204, 422), 14, MUTED, anchor='center')
    _panel(screen, pygame.Rect(436, 238, 408, 196))
    _text(screen, 'CUTTING MACHINE', (456, 251), 23, TEXT, True)
    _text(screen, 'CP-IO22 / GPIO', (456, 291), 16, MUTED)
    _status_light(screen, (464, 332), 'MOTORS', bool(gpio.get('cutting_motors_on')), gpio_ready, bool(gpio.get('cutting_motors_trip')))
    safety = 'TRIP ACTIVE' if gpio.get('cutting_motors_trip') else ('TRIP OK' if gpio_ready else 'TRIP --')
    _text(screen, safety, (640, 376), 27, RED if gpio.get('cutting_motors_trip') else GREEN if gpio_ready else AMBER, True, 'center')
    _text(screen, 'GPIO OFFLINE' if rpi.get('gpio_error') else 'GPIO ONLINE' if gpio_ready else 'INPUTS NOT CONFIGURED', (640, 418), 16, MUTED, anchor='center')
    for x, name, label, keys, unit, accent in (
        (16, 'electricity_meter', 'ELECTRICITY', ('energy_today_kwh', 'energy_month_kwh'), 'kWh', AMBER),
        (646, 'water_meter', 'WATER', ('water_today_m3', 'water_month_m3'), 'm³', CYAN)):
        _panel(screen, pygame.Rect(x, 446, 618, 94))
        device = _device(snapshot, name)
        online = bool(device.get('connected'))
        _text(screen, label, (x + 20, 460), 19, accent, True)
        _text(screen, 'ONLINE' if online else 'OFFLINE', (x + 20, 501), 16, GREEN if online else AMBER)
        for cx, key, period in ((x + 310, keys[0], 'TODAY'), (x + 508, keys[1], 'MONTH')):
            value = _value(device, key, None)
            _text(screen, period, (cx, 468), 15, MUTED, True, 'center')
            _text(screen, f"{'--' if value is None else value} {unit}", (cx, 507), 26, accent if online else MUTED, True, 'center', max_width=185)
    cip = rpi.get('cip', {})
    for i, (name, label) in enumerate((('gutting_left', 'LEFT'), ('cutting', 'CUTTING'), ('gutting_right', 'RIGHT'))):
        x = 16 + i * 420
        item = cip.get(name, {})
        active, enabled = bool(item.get('output')), bool(item.get('enable'))
        _panel(screen, pygame.Rect(x, 552, 408, 122), PANEL, GREEN if active else LINE)
        _text(screen, 'CIP / ' + label, (x + 20, 564), 21, TEXT, True)
        _text(screen, 'ACTIVE' if active else 'READY' if enabled else 'OFF', (x + 388, 576), 18, GREEN if active else MUTED, True, 'midright')
        for cx, key, period in ((x + 104, 'on_ms', 'ON'), (x + 304, 'off_ms', 'OFF')):
            _text(screen, period, (cx, 611), 15, MUTED, True, 'center')
            seconds = '--' if item.get(key) is None else f"{item[key] / 1000:.3f}".rstrip('0').rstrip('.')
            _text(screen, f"{seconds} s", (cx, 646), 26, TEXT, True, 'center', max_width=180)


def draw_dashboard(screen, snapshot, config, devices, rate_left, rate_right, elapsed=0):
    screen.fill(BG)
    # Update both meters on every frame, including during the system page.
    left = _side(snapshot, 'left', rate_left)
    right = _side(snapshot, 'right', rate_right)
    # Keep monitoring during Maintenance as well as Operation.
    now = time.monotonic()
    rpi = snapshot.get('rpi', {})
    target = rpi.get('productivity_setpoint', 0)
    target = target if isinstance(target, (int, float)) and math.isfinite(target) and 0 < target <= 10000 else 0
    for side, item, meter in (('left', left, rate_left), ('right', right, rate_right)):
        alarm = _productivity_alarm(meter, item['rate'], target, now,
                                    rpi.get('reset_sequence', 0),
                                    bool(_device(snapshot, 'vision_' + side).get('connected')))
        item['productivity_alarm'] = alarm
        # One blink per second, red for half a second then hidden.
        item['productivity_visible'] = not alarm or int(now * 2) % 2 == 0
    page, remaining = page_at(elapsed)
    _header(screen, snapshot, page, remaining)
    if page == 'statistics':
        _statistics(screen, snapshot, config, devices, left, right)
    else:
        _system(screen, snapshot, config, devices, left, right)


def run_dashboard(config, devices, state, stop_event, screenshot_event=None):
    _font.cache_clear()
    pygame.init()
    fullscreen = config.get("fullscreen", True)
    flags = pygame.FULLSCREEN if fullscreen else pygame.RESIZABLE
    display = pygame.display.set_mode((0, 0) if fullscreen else (1280, 720), flags)
    # Keep the same readable proportions on HD and Full HD 13-inch displays.
    screen = pygame.Surface((1280, 720))
    pygame.display.set_caption("Cutting / Gutting — Operation / Maintenance")
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
