# app/osm/nearby.py
import httpx
import math
from models import NearbyPlace

OVERPASS = "https://overpass-api.de/api/interpreter"


async def fetch_nearby_places(
    lat: float, lon: float,
    tag_type: str = "amenity",
    exclude_name: str = "",
    radius_m: int = 500,
    limit: int = 5,
) -> list:
    """
    Find similar POIs near the given coordinates using Overpass.
    Returns NearbyPlace objects with pre-computed quick confidence scores.
    Triggered when place not found in OSM or confidence < 50.
    """
    try:
        query = f"""
        [out:json][timeout:10];
        node(around:{radius_m},{lat},{lon})["{tag_type}"];
        out meta {limit * 3};
        """
        async with httpx.AsyncClient(
            timeout=12, headers={"User-Agent": "osm-sg-validator/1.0"}
        ) as client:
            resp = await client.post(OVERPASS, data={"data": query})
            elements = resp.json().get("elements", [])

        results = []
        for el in elements:
            tags = el.get("tags", {})
            name = tags.get("name", "")
            if not name or name.lower() == exclude_name.lower():
                continue

            el_lat = el.get("lat", lat)
            el_lon = el.get("lon", lon)
            dist = _haversine(lat, lon, el_lat, el_lon)

            ts = el.get("timestamp", "")
            edit_age_days = _estimate_age(ts)
            quick_confidence, quick_rec = _quick_score(edit_age_days, tags)

            results.append(NearbyPlace(
                name=name,
                osm_id=str(el.get("id")),
                lat=el_lat,
                lon=el_lon,
                distance_m=round(dist),
                category=tags.get(tag_type, "unknown"),
                confidence_score=quick_confidence,
                recommendation=quick_rec,
            ))

        # Sort by confidence descending, then distance
        results.sort(key=lambda x: (-x.confidence_score, x.distance_m))
        return results[:limit]

    except Exception:
        return []


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _estimate_age(timestamp: str) -> int:
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return 999


def _quick_score(edit_age_days: int, tags: dict) -> tuple:
    """Fast heuristic confidence score without running the full pipeline."""
    if edit_age_days < 180:
        return 0.85, "ACCEPT"
    elif edit_age_days < 365:
        return 0.72, "ACCEPT"
    elif edit_age_days < 730:
        return 0.55, "REVIEW"
    else:
        return 0.30, "REJECT"
