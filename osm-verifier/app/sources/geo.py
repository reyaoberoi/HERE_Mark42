import httpx
import aiosqlite
import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Optional

# Singapore bounding box
SG_BBOX = {
    "min_lat": 1.1304,
    "max_lat": 1.4784,
    "min_lon": 103.6065,
    "max_lon": 104.0860,
}

DB_PATH = "cache.db"

# ─────────────────────────────────────────
# 1. SQLite cache setup
# ─────────────────────────────────────────

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS coord_cache (
                name TEXT,
                postal_code TEXT,
                lat REAL,
                lon REAL,
                cached_at TEXT,
                PRIMARY KEY (name, postal_code)
            )
        """)
        await db.commit()

async def get_cached_coords(name: str, postal_code: str = "") -> Optional[tuple]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT lat, lon FROM coord_cache WHERE name=? AND postal_code=?",
            (name, postal_code)
        ) as cursor:
            row = await cursor.fetchone()
            return (row[0], row[1]) if row else None

async def save_coords(name: str, postal_code: str, lat: float, lon: float):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO coord_cache (name, postal_code, lat, lon, cached_at)
            VALUES (?, ?, ?, ?, ?)
        """, (name, postal_code, lat, lon, datetime.now(timezone.utc).isoformat()))
        await db.commit()

# ─────────────────────────────────────────
# 2. SG bounding box validator
# ─────────────────────────────────────────

def is_in_singapore(lat: float, lon: float) -> bool:
    return (
        SG_BBOX["min_lat"] <= lat <= SG_BBOX["max_lat"] and
        SG_BBOX["min_lon"] <= lon <= SG_BBOX["max_lon"]
    )

# ─────────────────────────────────────────
# 3. Nominatim geocode
# ─────────────────────────────────────────

async def geocode_nominatim(name: str, postal_code: str = "") -> Optional[tuple]:
    # Check cache first
    cached = await get_cached_coords(name, postal_code)
    if cached:
        print(f"[geo] cache hit for {name}")
        return cached

    query = f"{name}, Singapore {postal_code}".strip()
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": query,
        "format": "json",
        "limit": 1,
        "countrycodes": "sg",
        "viewbox": f"{SG_BBOX['min_lon']},{SG_BBOX['max_lat']},{SG_BBOX['max_lon']},{SG_BBOX['min_lat']}",
        "bounded": 1,
    }
    headers = {"User-Agent": "osm-verifier/1.0"}

    async with httpx.AsyncClient(timeout=5) as client:
        try:
            r = await client.get(url, params=params, headers=headers)
            results = r.json()
            if not results:
                return None
            lat = float(results[0]["lat"])
            lon = float(results[0]["lon"])
            if not is_in_singapore(lat, lon):
                return None
            await save_coords(name, postal_code, lat, lon)
            return (lat, lon)
        except Exception as e:
            print(f"[geo] nominatim error: {e}")
            return None

# ─────────────────────────────────────────
# 4. Overpass — find OSM nodes within 100m
# ─────────────────────────────────────────

async def query_overpass_nearby(lat: float, lon: float, radius: int = 300) -> list:
    query = f"""
    [out:json][timeout:10];
    (
      node(around:{radius},{lat},{lon});
    );
    out meta;
    """
    url = "https://overpass-api.de/api/interpreter"

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.post(url, data={"data": query})
            data = r.json()
            return data.get("elements", [])
        except Exception as e:
            print(f"[geo] overpass error: {e}")
            return []

# ─────────────────────────────────────────
# 5. OSM edit age extraction
# ─────────────────────────────────────────

def extract_edit_age_days(node: dict) -> Optional[float]:
    timestamp = node.get("timestamp")  # e.g. "2021-03-15T10:22:00Z"
    if not timestamp:
        return None
    try:
        edited_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - edited_at).days
        return age
    except Exception:
        return None

# ─────────────────────────────────────────
# 6. Contributor edit count from Changeset API
# ─────────────────────────────────────────

async def get_contributor_edit_count(username: str) -> Optional[int]:
    if not username:
        return None
    url = f"https://api.openstreetmap.org/api/0.6/changesets.json"
    params = {"display_name": username, "limit": 100}
    headers = {"User-Agent": "osm-verifier/1.0"}

    async with httpx.AsyncClient(timeout=5) as client:
        try:
            r = await client.get(url, params=params, headers=headers)
            data = r.json()
            changesets = data.get("changesets", [])
            return len(changesets)  # count of recent changesets (max 100)
        except Exception as e:
            print(f"[geo] changeset api error: {e}")
            return None

# ─────────────────────────────────────────
# 7. Main geo signal — ties everything together
# ─────────────────────────────────────────

async def get_geo_signal(name: str, lat: float, lon: float, postal_code: str = "") -> dict:
    await init_db()

    if not is_in_singapore(lat, lon):
        return {
            "source": "geo",
            "signal": "unknown",
            "confidence": 0.0,
            "detail": "Coordinates outside Singapore bbox"
        }

    # Progressive widening: try 300m first, then 500m
    nodes = await query_overpass_nearby(lat, lon, radius=300)
    if not nodes:
        nodes = await query_overpass_nearby(lat, lon, radius=500)

    if not nodes:
        return {
            "source": "geo",
            "signal": "unknown",
            "confidence": 0.2,
            "detail": "No OSM nodes found within 500m"
        }

    # Find the best matching node by name tag
# Find the best matching node by name tag
    matched_node = None
    for node in nodes:
        tags = node.get("tags", {})
        node_name = tags.get("name", "").lower()
        if name.lower() in node_name or node_name in name.lower():
            matched_node = node
            break

    # Fall back to closest node if no name match
    if not matched_node and nodes:
        matched_node = nodes[0]

    tags = matched_node.get("tags", {})
    edit_age_days = extract_edit_age_days(matched_node)
    username = matched_node.get("user", "")
    edit_count = await get_contributor_edit_count(username)

    # Check for disused/closed tags
    is_disused = any(k.startswith("disused:") or k.startswith("abandoned:") 
                     for k in tags.keys())
    has_closed_tag = tags.get("opening_hours") == "closed" or \
                     tags.get("disused") == "yes"

    # Build signal
    if is_disused or has_closed_tag:
        signal = "closed"
        confidence = 0.75
        detail = f"Node has disused/closed tag. Last edited {edit_age_days} days ago."
    elif edit_age_days is not None and edit_age_days < 180:
        signal = "active"
        confidence = 0.65
        detail = f"Node edited recently ({edit_age_days} days ago) by {username} ({edit_count} changesets)"
    elif edit_age_days is not None and edit_age_days > 730:
        signal = "unknown"
        confidence = 0.4
        detail = f"Node not edited in {edit_age_days} days — stale data likely"
    else:
        signal = "unknown"
        confidence = 0.5
        detail = f"Node age: {edit_age_days} days, contributor changesets: {edit_count}"

    return {
        "source": "geo",
        "signal": signal,
        "confidence": confidence,
        "detail": detail,
        "meta": {
            "edit_age_days": edit_age_days,
            "username": username,
            "edit_count": edit_count,
            "node_id": matched_node.get("id"),
            "tags": tags,
        }
    }