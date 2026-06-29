"""IP geolocation via Amap (高德) REST API. Falls back gracefully."""

import httpx

from config import settings


async def get_location() -> dict | None:
    """Return {city, province, country, adcode} or None on failure."""
    if not settings.amap_api_key:
        return None

    # Manual override
    if settings.user_city:
        return {
            "city": settings.user_city,
            "province": "",
            "country": "中国",
            "adcode": "",
        }

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                "https://restapi.amap.com/v3/ip",
                params={"key": settings.amap_api_key, "ip": ""},
            )
            data = resp.json()
            if data.get("status") != "1" or data.get("infocode") != "10000":
                print(f"[Location] Amap IP lookup failed: {data}", flush=True)
                return None

            province = data.get("province", "")
            city = data.get("city", "") or province
            adcode = data.get("adcode", "")

            if not city:
                return None

            # Cache to in-memory config (will be saved to user_config by caller)
            print(f"[Location] resolved: {city}, {province}", flush=True)
            return {
                "city": city,
                "province": province,
                "country": "中国",
                "adcode": adcode,
            }
    except Exception as e:
        print(f"[Location] failed: {e}", flush=True)
        return None
