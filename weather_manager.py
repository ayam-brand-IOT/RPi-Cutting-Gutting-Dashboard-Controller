from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request


class WeatherManager(threading.Thread):
    """Récupère périodiquement la météo Open-Meteo sans dépendance externe."""

    def __init__(self, config, state, stop_event):
        super().__init__(name="weather", daemon=True)
        self.config = config
        self.state = state
        self.stop_event = stop_event

    @staticmethod
    def _condition(code):
        code = int(code)
        if code == 0:
            return "sunny"
        if code in (1, 2, 3, 45, 48):
            return "cloudy"
        if code in (95, 96, 99):
            return "storm"
        if code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82):
            return "rain"
        if code in (71, 73, 75, 77, 85, 86):
            return "snow"
        return "unknown"

    def _fetch(self):
        query = urllib.parse.urlencode({
            "latitude": float(self.config["latitude"]),
            "longitude": float(self.config["longitude"]),
            "current": "temperature_2m,weather_code",
            "timezone": "auto",
        })
        url = f"https://api.open-meteo.com/v1/forecast?{query}"
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "cutting-gutting-rpi/1.0"},
        )
        with urllib.request.urlopen(
            request, timeout=float(self.config.get("timeout_s", 8))
        ) as response:
            data = json.loads(response.read().decode("utf-8"))
        current = data["current"]
        temperature = round(float(current["temperature_2m"]), 1)
        condition = self._condition(current.get("weather_code", -1))
        self.state.update_weather(temperature, condition)

    def run(self):
        update_s = max(60.0, float(self.config.get("update_s", 600)))
        while not self.stop_event.is_set():
            try:
                self._fetch()
            except Exception as error:
                print(f"[WEATHER] API indisponible: {error}", flush=True)
            if self.stop_event.wait(update_s):
                break
