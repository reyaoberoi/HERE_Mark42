# app/sources/geo.py
import httpx
import sqlite3
import json
from datetime import datetime
from pathlib import Path
import re

NOMINATIM = "https://nominatim.openstreetmap.org/search"
OVERPASS  = "https://overpass-api.de/api/interpreter"

SG_BBOX = (1.2, 103.6, 1.5, 104.0)  # min_lat, min_lon, max_lat, max_lon

HEADERS = {"User-Agent": "osm-sg-validator/1.0 (hackathon)"}
CACHE_DB_PATH = str(Path(__file__).resolve().parents[2] / "cache.db")


def _cache_get(key: str):
    try:
        conn = sqlite3.connect(CACHE_DB_PATH)
        conn.execute("CREATE TABLE IF NOT EXISTS geo_cache (key TEXT PRIMARY KEY, value TEXT, created_at TEXT)")
        row = conn.execute("SELECT value FROM geo_cache WHERE key=?", (key,)).fetchone()
        conn.close()
        return json.loads(row[0]) if row else None
    except Exception:
        return None


def _cache_set(key: str, value: dict):
    try:
        conn = sqlite3.connect(CACHE_DB_PATH)
        conn.execute("CREATE TABLE IF NOT EXISTS geo_cache (key TEXT PRIMARY KEY, value TEXT, created_at TEXT)")
        conn.execute("INSERT OR REPLACE INTO geo_cache VALUES (?,?,?)",
                     (key, json.dumps(value), datetime.utcnow().isoformat()))
        conn.commit()
        conn.close()
    except Exception:
        pass


async def fetch_geo(name: str, address: str) -> dict:
    cache_key = f"geo:{name}:{address}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    query = f"{name} {address} Singapore"

    async with httpx.AsyncClient(timeout=10, headers=HEADERS) as client:
        # 1. Nominatim geocode
        try:
            resp = await client.get(NOMINATIM, params={
                "q": query, "format": "json",
                "countrycodes": "sg", "limit": 1
            })
            results = resp.json()
        except Exception:
            results = []

        if not results:
            # Try name-only fallback
            try:
                resp = await client.get(NOMINATIM, params={
                    "q": f"{name} Singapore",
                    "format": "json", "countrycodes": "sg", "limit": 1
                })
                results = resp.json()
            except Exception:
                results = []

        if not results:
            return {"lat": None, "lon": None, "osm_found": False}

        hit = results[0]
        lat = float(hit["lat"])
        lon = float(hit["lon"])

        # Validate SG bounding box
        if not (SG_BBOX[0] <= lat <= SG_BBOX[2] and SG_BBOX[1] <= lon <= SG_BBOX[3]):
            return {"lat": None, "lon": None, "osm_found": False}

        # 2. Overpass: get full tags + meta for nearest POI node
        safe_name = re.escape(name.strip())
        overpass_q = f"""
        [out:json][timeout:10];
        (
          node(around:120,{lat},{lon})["name"~"{safe_name}",i];
          way(around:120,{lat},{lon})["name"~"{safe_name}",i];
          rel(around:120,{lat},{lon})["name"~"{safe_name}",i];
        );
        out center meta 12;
        """
        osm_node = {}
        try:
            r2 = await client.post(OVERPASS, data={"data": overpass_q})
            elements = r2.json().get("elements", [])
            if elements:
                def _coords(elm):
                    e_lat = elm.get("lat")
                    e_lon = elm.get("lon")
                    if e_lat is None or e_lon is None:
                        c = elm.get("center") or {}
                        e_lat = c.get("lat")
                        e_lon = c.get("lon")
                    return e_lat, e_lon

                best = None
                best_dist = 999999.0
                for el in elements:
                    e_lat, e_lon = _coords(el)
                    if e_lat is None or e_lon is None:
                        continue
                    dist = abs(float(e_lat) - lat) + abs(float(e_lon) - lon)
                    if dist < best_dist:
                        best_dist = dist
                        best = el

                el = best or elements[0]
                e_lat, e_lon = _coords(el)
                osm_node = {
                    "osm_id": str(el.get("id")),
                    "osm_type": el.get("type"),
                    "tags": el.get("tags", {}),
                    "version": el.get("version", 1),
                    "timestamp": el.get("timestamp", ""),
                    "lat": e_lat,
                    "lon": e_lon,
                }
        except Exception:
            pass

        # 3. Edit age from timestamp
        edit_age_days = None
        tags = osm_node.get("tags", {})
        tag_type = _infer_tag_type(tags)

        ts = osm_node.get("timestamp", "")
        if ts:
            try:
                from datetime import timezone
                edited = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                edit_age_days = (datetime.now(timezone.utc) - edited).days
            except Exception:
                pass

        result = {
            "lat": float(osm_node.get("lat") or lat),
            "lon": float(osm_node.get("lon") or lon),
            "osm_found": bool(osm_node),
            "osm_id": osm_node.get("osm_id"),
            "osm_type": osm_node.get("osm_type"),
            "tags": tags,
            "website": tags.get("website", None),
            "tag_type": tag_type,
            "edit_age_days": edit_age_days,
            "postal_code": _extract_postal(address),
            "website_url": tags.get("website") or tags.get("contact:website"),
            "version": osm_node.get("version", 1),
        }
        _cache_set(cache_key, result)
        return result


def _infer_tag_type(tags: dict) -> str:
    for key in ["shop", "amenity", "tourism", "leisure"]:
        if key in tags:
            return key
    return "amenity"


def _extract_postal(address: str) -> str:
    import re
    match = re.search(r"\b\d{6}\b", address)
    return match.group(0) if match else ""