"""Weather via Amap (高德) REST API. Falls back gracefully."""

import httpx

from config import settings


async def get_weather(adcode: str = "", city: str = "") -> dict | None:
    """Return {condition, temp, humidity, wind} or None on failure.

    Prefer adcode (more precise). Fall back to city name.
    """
    if not settings.amap_api_key or not settings.weather_enabled:
        return None

    params = {
        "key": settings.amap_api_key,
        "extensions": "base",
    }
    if adcode:
        params["city"] = adcode
    elif city:
        params["city"] = city
    else:
        return None

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                "https://restapi.amap.com/v3/weather/weatherInfo",
                params=params,
            )
            data = resp.json()
            if data.get("status") != "1" or data.get("infocode") != "10000":
                print(f"[Weather] Amap weather lookup failed: {data}", flush=True)
                return None

            lives = data.get("lives", [])
            if not lives:
                return None

            live = lives[0]
            result = {
                "condition": live.get("weather", ""),
                "temp": live.get("temperature", ""),
                "humidity": live.get("humidity", ""),
                "wind": live.get("winddirection", "") + "风" + live.get("windpower", ""),
                "city": live.get("city", city),
            }
            print(f"[Weather] {result['city']}: {result['condition']} {result['temp']}°C", flush=True)
            return result
    except Exception as e:
        print(f"[Weather] failed: {type(e).__name__}: {e!r}", flush=True)
        return None
