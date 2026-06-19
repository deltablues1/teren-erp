"""OpenWeatherMap wrapper - dohvaća dnevne vremenske prilike za lokaciju projekta."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from config import OPENWEATHER_API_KEY

log = logging.getLogger(__name__)

GEO_URL = "https://api.openweathermap.org/geo/1.0/direct"
WEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"


def is_available() -> bool:
    return bool(OPENWEATHER_API_KEY)


def get_current_weather(grad: str) -> dict[str, Any] | None:
    """Vrati trenutno vrijeme za grad. None ako API ključ nije konfiguriran."""
    if not is_available():
        return None

    try:
        with httpx.Client(timeout=10.0) as client:
            geo_resp = client.get(GEO_URL, params={
                "q": f"{grad},HR",
                "limit": 1,
                "appid": OPENWEATHER_API_KEY,
            })
            geo_resp.raise_for_status()
            geo = geo_resp.json()
            if not geo:
                log.warning("OpenWeather geo nije pronašao grad %s", grad)
                return None

            lat, lon = geo[0]["lat"], geo[0]["lon"]
            w_resp = client.get(WEATHER_URL, params={
                "lat": lat, "lon": lon,
                "units": "metric",
                "lang": "hr",
                "appid": OPENWEATHER_API_KEY,
            })
            w_resp.raise_for_status()
            data = w_resp.json()

        return {
            "min_temp": data["main"].get("temp_min"),
            "max_temp": data["main"].get("temp_max"),
            "oborine_mm": data.get("rain", {}).get("1h", 0),
            "opis": data["weather"][0].get("description", ""),
        }
    except (httpx.HTTPError, KeyError, IndexError) as e:
        log.warning("OpenWeather greška za %s: %s", grad, e)
        return None
