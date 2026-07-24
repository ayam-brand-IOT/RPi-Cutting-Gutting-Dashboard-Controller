import json
import threading
from unittest.mock import patch

from state import StateStore
from weather_manager import WeatherManager


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps({
            "current": {"temperature_2m": 28.6, "weather_code": 61}
        }).encode("utf-8")


def main():
    state = StateStore([])
    manager = WeatherManager(
        {"latitude": 4.85, "longitude": 100.74, "timeout_s": 2},
        state,
        threading.Event(),
    )
    with patch("urllib.request.urlopen", return_value=FakeResponse()):
        manager._fetch()
    weather = state.snapshot()["weather"]
    assert weather["temperature_c"] == 28.6
    assert weather["condition"] == "rain"
    print("Weather API OK — température et code WMO décodés")


if __name__ == "__main__":
    main()
