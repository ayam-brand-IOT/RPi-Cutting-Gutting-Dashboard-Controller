from __future__ import annotations

import collections
import functools
import time

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


class ProductivityMeter:
    """Calcule un débit glissant à partir de compteurs cumulatifs."""

    def __init__(self, window_s=60.0):
        self.window_s = float(window_s)
        self.samples = collections.deque()
        self.last_total = None

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
        if len(self.samples) < 2:
            return 0.0
        elapsed = self.samples[-1][0] - self.samples[0][0]
        if elapsed <= 0:
            return 0.0
        return max(0.0, (self.samples[-1][1] - self.samples[0][1]) * 60.0 / elapsed)


def _people_count(snapshot, config):
    """Cherche le compteur configuré puis quelques noms usuels."""
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
    _text(screen, title.upper(), (rect.x + 18, rect.y + 16), 12, MUTED, True)
    _text(screen, value, (rect.centerx, rect.centery + (8 if hero else 3)),
          58 if hero else 38, color, True, "center")
    _text(screen, subtitle, (rect.centerx, rect.bottom - 18), 12, MUTED,
          False, "midbottom")


def _alarm_banner(screen, rect, trips, offline):
    if trips:
        pygame.draw.rect(screen, RED_DARK, rect, border_radius=14)
        pygame.draw.rect(screen, RED, rect, 2, border_radius=14)
        _text(screen, "⚠  TRIP MOTEUR", (rect.x + 20, rect.centery), 22,
              RED, True, "midleft")
        _text(screen, "  •  ".join(trips), (rect.right - 20, rect.centery),
              18, TEXT, True, "midright")
    elif offline:
        pygame.draw.rect(screen, (68, 50, 22), rect, border_radius=14)
        pygame.draw.rect(screen, AMBER, rect, 1, border_radius=14)
        _text(screen, "COMMUNICATION DÉGRADÉE", (rect.x + 20, rect.centery),
              17, AMBER, True, "midleft")
        _text(screen, "  •  ".join(offline), (rect.right - 20, rect.centery),
              15, TEXT, False, "midright")
    else:
        pygame.draw.rect(screen, (14, 48, 41), rect, border_radius=14)
        pygame.draw.rect(screen, (26, 92, 73), rect, 1, border_radius=14)
        _text(screen, "✓  PRODUCTION NORMALE", (rect.x + 20, rect.centery),
              17, GREEN, True, "midleft")
        _text(screen, "Aucun trip moteur", (rect.right - 20, rect.centery),
              14, MUTED, False, "midright")


def _machine_card(screen, rect, title, device):
    values = device.get("values", {})
    online = bool(device.get("connected"))
    trip = bool(values.get("motors_trip"))
    color = RED_DARK if trip else PANEL
    border = RED if trip else LINE
    _panel(screen, rect, color, border)
    _text(screen, title, (rect.x + 16, rect.y + 14), 17, TEXT, True)
    status_rect = pygame.Rect(rect.right - 118, rect.y + 11, 102, 27)
    if trip:
        _pill(screen, status_rect, "TRIP MOTEUR", fault=True)
    elif not online:
        _pill(screen, status_rect, "HORS LIGNE", fault=True)
    else:
        _pill(screen, status_rect, "EN LIGNE", active=True)

    motor_on = bool(values.get("motors_on"))
    belt_on = bool(values.get("belt_on"))
    _text(screen, "MOTEURS", (rect.x + 17, rect.y + 54), 11, MUTED, True)
    _text(screen, "ON" if motor_on else "ARRÊT", (rect.x + 17, rect.y + 73),
          18, GREEN if motor_on else MUTED, True)
    _text(screen, "CONVOYEUR", (rect.x + 118, rect.y + 54), 11, MUTED, True)
    _text(screen, "ON" if belt_on else "ARRÊT", (rect.x + 118, rect.y + 73),
          18, GREEN if belt_on else MUTED, True)

    rpms = (("Lame", values.get("rpm_blade", 0)),
            ("Roue 1", values.get("rpm_wheel1", 0)),
            ("Roue 2", values.get("rpm_wheel2", 0)))
    rpm_x = rect.x + 225
    available = rect.right - rpm_x - 12
    for index, (label, value) in enumerate(rpms):
        cx = rpm_x + int(available * (index + 0.5) / 3)
        _text(screen, label, (cx, rect.y + 56), 11, MUTED, False, "center")
        _text(screen, value, (cx, rect.y + 81), 21, CYAN, True, "center")
        _text(screen, "RPM", (cx, rect.y + 100), 9, MUTED, False, "center")


def _cip_card(screen, rect, title, item):
    enabled = bool(item.get("enable"))
    output = bool(item.get("output"))
    _panel(screen, rect, (15, 31, 45) if output else PANEL_2,
           GREEN if output else LINE)
    pygame.draw.circle(screen, GREEN if output else (60, 76, 95),
                       (rect.x + 19, rect.y + 21), 7)
    _text(screen, title, (rect.x + 34, rect.y + 21), 15, TEXT, True, "midleft")
    _pill(screen, pygame.Rect(rect.right - 91, rect.y + 9, 78, 25),
          "ACTIF" if output else ("PRÊT" if enabled else "OFF"),
          active=enabled or output)
    _text(screen, "SORTIE CP-IO22", (rect.x + 16, rect.y + 49), 10, MUTED, True)
    _text(screen, "OUVERTE" if output else "FERMÉE", (rect.x + 16, rect.y + 67),
          18, GREEN if output else TEXT, True)
    _text(screen, f"ON {item.get('on_ms', '--')} ms  •  OFF {item.get('off_ms', '--')} ms",
          (rect.right - 14, rect.bottom - 14), 11, MUTED, False, "bottomright")


def run_dashboard(config, devices, state, stop_event):
    pygame.init()
    fullscreen = config.get("fullscreen", True)
    flags = pygame.FULLSCREEN if fullscreen else pygame.RESIZABLE
    screen = pygame.display.set_mode((0, 0) if fullscreen else (1280, 720), flags)
    pygame.display.set_caption("Cutting / Gutting — Production")
    pygame.mouse.set_visible(not fullscreen)
    print(f"[DISPLAY] pilote={pygame.display.get_driver()} "
          f"résolution={screen.get_width()}x{screen.get_height()}", flush=True)

    clock = pygame.time.Clock()
    fps = int(config.get("fps", 15))
    rate = ProductivityMeter(config.get("productivity_window_s", 60))

    try:
        while not stop_event.is_set():
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    stop_event.set()
                elif event.type == pygame.KEYDOWN and event.key in (pygame.K_ESCAPE, pygame.K_q):
                    stop_event.set()

            snapshot = state.snapshot()
            w, h = screen.get_size()
            screen.fill(BG)
            margin, gap = 22, 12

            left = _device(snapshot, "vision_left")
            right = _device(snapshot, "vision_right")
            gut_left = _device(snapshot, "gutting_left")
            gut_right = _device(snapshot, "gutting_right")
            total = int(_value(left, "fish_counter", 0)) + int(_value(right, "fish_counter", 0))
            bad = int(_value(left, "ejected_fish", 0)) + int(_value(right, "ejected_fish", 0))
            good = max(0, total - bad)
            belly = int(_value(gut_left, "ejector_count", 0)) + int(_value(gut_right, "ejector_count", 0))
            fish_min = rate.update(total)
            people = _people_count(snapshot, config)

            _text(screen, "PRODUCTION  •  CUTTING / GUTTING", (margin, 16), 23, TEXT, True)
            _text(screen, time.strftime("%d/%m/%Y   %H:%M:%S"),
                  (w - margin, 19), 15, MUTED, False, "topright")
            mqtt_ok = snapshot.get("rpi", {}).get("mqtt_connected", False)
            _pill(screen, pygame.Rect(w - margin - 105, 43, 105, 25),
                  "MQTT OK" if mqtt_ok else "MQTT OFF", active=mqtt_ok, fault=not mqtt_ok)

            trips = []
            offline = []
            for name, label in (("gutting_left", "Gutting gauche"),
                                ("gutting_right", "Gutting droite")):
                item = _device(snapshot, name)
                if bool(_value(item, "motors_trip", 0)):
                    trips.append(label)
                if not item.get("connected"):
                    offline.append(label)
            banner = pygame.Rect(margin, 77, w - 2 * margin, 52)
            _alarm_banner(screen, banner, trips, offline)

            kpi_y, kpi_h = 141, 174
            available = w - 2 * margin - 4 * gap
            widths = [int(available * 0.27), int(available * 0.16)]
            remaining = available - sum(widths)
            widths += [remaining // 3, remaining // 3, remaining - 2 * (remaining // 3)]
            titles = (("Productivité", f"{fish_min:.1f}", "poissons / minute", BLUE, True),
                      ("Personnes", "--" if people is None else people,
                       "présentes", CYAN if people is not None else MUTED, False),
                      ("Good", good, "poissons conformes", GREEN, False),
                      ("Bad", bad, "éjections vision", RED if bad else AMBER, False),
                      ("Belly", belly, "éjections orientation", AMBER, False))
            x = margin
            for width, item in zip(widths, titles):
                _kpi(screen, pygame.Rect(x, kpi_y, width, kpi_h), *item)
                x += width + gap

            machine_y = kpi_y + kpi_h + gap
            machine_h = 118
            machine_w = (w - 2 * margin - gap) // 2
            _machine_card(screen, pygame.Rect(margin, machine_y, machine_w, machine_h),
                          devices.get("gutting_left", {}).get("label", "Gutting gauche"), gut_left)
            _machine_card(screen, pygame.Rect(margin + machine_w + gap, machine_y,
                                               w - 2 * margin - gap - machine_w, machine_h),
                          devices.get("gutting_right", {}).get("label", "Gutting droite"), gut_right)

            cip_title_y = machine_y + machine_h + 13
            _text(screen, "CIP — SORTIES RASPBERRY PI / CP-IO22", (margin, cip_title_y),
                  13, MUTED, True)
            cip_y = cip_title_y + 25
            cip_h = max(92, h - cip_y - margin)
            cip_w = (w - 2 * margin - 2 * gap) // 3
            cip_data = snapshot.get("rpi", {}).get("cip", {})
            cip_items = (("Gutting gauche", "gutting_left"),
                         ("Cutting machine", "cutting"),
                         ("Gutting droite", "gutting_right"))
            for index, (label, name) in enumerate(cip_items):
                x = margin + index * (cip_w + gap)
                width = cip_w if index < 2 else w - margin - x
                _cip_card(screen, pygame.Rect(x, cip_y, width, cip_h),
                          label, cip_data.get(name, {}))

            pygame.display.flip()
            clock.tick(fps)
    finally:
        pygame.quit()
