"""Location via Amap (高德) REST API — IP fallback + reverse geocoding."""

import httpx

from config import settings


async def geocode_reverse(lat: float, lng: float) -> dict | None:
    """Reverse geocode coordinates via Amap. Returns {city, province, district, adcode, address} or None."""
    if not settings.amap_api_key:
        print("[Location] no AMAP_API_KEY configured", flush=True)
        return None

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                "https://restapi.amap.com/v3/geocode/regeo",
                params={
                    "key": settings.amap_api_key,
                    "location": f"{lng},{lat}",
                    "extensions": "base",
                },
            )
            data = resp.json()

            if data.get("status") != "1":
                print(f"[Location] regeo API error: {data}", flush=True)
                return None

            regeo = data.get("regeocode", {})
            comp = regeo.get("addressComponent", {})
            if not comp:
                print("[Location] regeo returned empty addressComponent", flush=True)
                return None

            city = comp.get("city", "") or comp.get("province", "")
            province = comp.get("province", "")
            district = comp.get("district", "")
            adcode = comp.get("adcode", "")
            address = regeo.get("formatted_address", "")

            if not city:
                print("[Location] regeo could not resolve city", flush=True)
                return None

            print(f"[Location] geocoded: {address}", flush=True)
            return {
                "city": city,
                "province": province,
                "district": district,
                "country": comp.get("country", "中国"),
                "adcode": adcode,
                "address": address,
            }
    except Exception as e:
        print(f"[Location] regeo failed: {type(e).__name__}: {e}", flush=True)
        return None


async def get_location() -> dict | None:
    """Return {city, province, country, adcode} from IP or manual config.
    Browser geolocation is preferred; USER_CITY is the last-resort fallback."""
    if not settings.amap_api_key:
        return None

    # IP geolocation fallback
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                "https://restapi.amap.com/v3/ip",
                params={"key": settings.amap_api_key, "ip": ""},
            )
            data = resp.json()

            if data.get("status") != "1":
                return None

            province = data.get("province", "")
            city = data.get("city", "")
            adcode = data.get("adcode", "")

            if isinstance(city, list):
                city = city[0] if city else ""
            if isinstance(province, list):
                province = province[0] if province else ""
            if isinstance(adcode, list):
                adcode = adcode[0] if adcode else ""

            if not city:
                city = province

            if not city:
                return None

            return {
                "city": city,
                "province": province,
                "country": "中国",
                "adcode": adcode,
            }
    except Exception:
        return None


async def search_nearby_places(
    lat: float, lng: float, keywords: str = "", radius: int = 3000, limit: int = 8
) -> list[dict]:
    """Search nearby POIs via Amap place/around API. Returns list of {name, type, address, distance, rating, cost}."""
    if not settings.amap_api_key:
        return []

    kw = keywords or "餐饮|美食|小吃"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                "https://restapi.amap.com/v3/place/around",
                params={
                    "key": settings.amap_api_key,
                    "location": f"{lng},{lat}",
                    "keywords": kw,
                    "radius": str(radius),
                    "offset": str(limit),
                    "page": "1",
                    "extensions": "all",
                },
            )
            data = resp.json()
            if data.get("status") != "1":
                print(f"[Nearby] API error: {data}", flush=True)
                return []

            pois = data.get("pois", [])
            results = []
            for p in pois:
                biz = p.get("biz_ext", {}) or {}
                results.append({
                    "name": p.get("name", ""),
                    "type": p.get("type", ""),
                    "address": p.get("address", ""),
                    "distance": p.get("distance", ""),
                    "rating": biz.get("rating", "") or p.get("deep_info", {}).get("rating", ""),
                    "cost": biz.get("cost", "") or p.get("deep_info", {}).get("avg_price", ""),
                })

            print(f"[Nearby] found {len(results)} places for kw='{kw}'", flush=True)
            return results
    except Exception as e:
        # httpx.ConnectError can carry an empty message — always log the type
        print(f"[Nearby] failed: {type(e).__name__}: {e}", flush=True)
        return []


def nearby_to_context(places: list[dict], max_items: int = 5) -> str:
    """Format POI results as a compact text block for AI context."""
    if not places:
        return ""

    lines = ["周边推荐："]
    for i, p in enumerate(places[:max_items]):
        parts = [f"{i+1}. {p['name']}"]
        if p["rating"]:
            parts.append(f"评分{p['rating']}")
        if p["cost"]:
            parts.append(f"人均{p['cost']}元")
        if p["distance"]:
            parts.append(f"距你{p['distance']}米")
        parts.append(f"({p['address']})")
        lines.append(" ".join(parts))

    return "\n".join(lines)
