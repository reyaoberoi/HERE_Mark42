# main.py — OSM Singapore POI Freshness Engine
import asyncio
import hashlib
import json
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from models import NearbyPlace, SourceSignal, VerifyRequest, VerifyResponse
from app.sources.geo import fetch_geo
from app.sources.gov_data import fetch_gov_data
from app.sources.food_platforms import fetch_food_platforms
from app.sources.social_signals import fetch_social_signals
from app.sources.mapillary import fetch_mapillary
from app.sources.wikidata import fetch_wikidata
from app.sources.wayback import fetch_wayback
from app.sources.tripadvisor import fetch_tripadvisor
from app.sources.singapore_gov_live import fetch_sg_gov_live
from app.scorer.weighted_scorer import compute_score, build_narrative, generate_changeset_diff
from app.scorer.stats import get_staleness_context
from app.osm.nearby import fetch_nearby_places
from app.osm.changeset import submit_osm_changeset

HEATMAP_CACHE = []

# ── SQLite result cache ──────────────────────────────────────────────────────
def _cache_db():
    conn = sqlite3.connect("cache.db")
    conn.execute("""CREATE TABLE IF NOT EXISTS verify_cache (
        key TEXT PRIMARY KEY,
        result TEXT,
        created_at TEXT
    )""")
    conn.commit()
    return conn


def _cache_get(key: str):
    conn = _cache_db()
    row = conn.execute(
        "SELECT result, created_at FROM verify_cache WHERE key=?", (key,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    created = datetime.fromisoformat(row[1])
    age_h = (datetime.now(timezone.utc) - created.replace(tzinfo=timezone.utc)).total_seconds() / 3600
    if age_h > 24:
        return None
    return json.loads(row[0])


def _cache_set(key: str, data: dict):
    conn = _cache_db()
    conn.execute(
        "INSERT OR REPLACE INTO verify_cache VALUES (?,?,?)",
        (key, json.dumps(data), datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


# ── Score helpers ────────────────────────────────────────────────────────────
def _current_confidence(signals: list) -> int:
    """Estimate current confidence from signals gathered so far."""
    if not signals:
        return 0
    actives = sum(1 for s in signals if s.get("status") == "ACTIVE")
    closeds = sum(1 for s in signals if s.get("status") == "CLOSED")
    if closeds > actives:
        return max(0, 100 - closeds * 20)
    weighted = sum(s.get("confidence", 0) for s in signals if s.get("status") == "ACTIVE")
    return min(99, int(weighted / max(len(signals), 1) * 130))


def _build_summary(
    name: str, address: str, lat, lon, osm_found: bool,
    predicted_status: str, confidence: int,
    confirmed_from: list, recommendation: str,
    db_detail: str,
) -> str:
    coord_str = f"{lat:.4f}, {lon:.4f}" if lat and lon else "unknown"
    db_str = db_detail if db_detail else ("Found" if osm_found else "Not Found")
    src_str = ", ".join(confirmed_from) if confirmed_from else "no sources confirmed"
    return (
        f"Place Name: {name}\n"
        f"Address: {address}\n"
        f"Coordinates: {coord_str}\n"
        f"Match in Database: {db_str}\n"
        f"Predicted Status: {predicted_status}\n"
        f"Confidence: {confidence}%\n"
        f"Confirmed From: {src_str}\n"
        f"Recommendation: {recommendation}"
    )


# ── Lifespan ─────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        with open("heatmap.json", "r") as f:
            global HEATMAP_CACHE
            HEATMAP_CACHE = json.load(f)
        print(f"Heatmap loaded: {len(HEATMAP_CACHE)} nodes")
    except FileNotFoundError:
        print("heatmap.json not found — run build_stats.py first")
    yield


app = FastAPI(title="OSM Singapore POI Freshness Engine", version="1.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

try:
    app.mount("/static", StaticFiles(directory="../frontend"), name="static")
except Exception:
    pass


@app.get("/", response_class=HTMLResponse)
async def root():
    try:
        with open("../frontend/index.html") as f:
            return HTMLResponse(f.read())
    except Exception:
        return HTMLResponse("<h1>OSM SG POI Freshness Engine</h1><p>See <a href='/docs'>/docs</a></p>")


@app.get("/health")
async def health():
    return {"status": "ok", "heatmap_nodes": len(HEATMAP_CACHE)}


# ── Main /verify endpoint with early-exit cascading pipeline ─────────────────
@app.post("/verify", response_model=VerifyResponse)
async def verify(req: VerifyRequest):
    cache_key = hashlib.sha256(f"{req.name.lower()}|{req.address.lower()}".encode()).hexdigest()
    cached = _cache_get(cache_key)
    if cached:
        return VerifyResponse(**cached)

    # ── TIER 1: Fast geo resolution (~0.5s) ──────────────────────────────────
    geo = await fetch_geo(req.name, req.address)
    lat = geo.get("lat") or 1.3521
    lon = geo.get("lon") or 103.8198
    osm_id = geo.get("osm_id")
    osm_found = geo.get("osm_found", False)
    edit_age_days = geo.get("edit_age_days")
    tag_type = geo.get("tag_type", "amenity")

    geo_signal = {
        "source": "osm_geo",
        "status": "ACTIVE" if osm_found else "UNKNOWN",
        "confidence": 0.7 if osm_found else 0.0,
        "detail": geo.get("detail", ""),
    }
    signals = [geo_signal]

    early_conf = _current_confidence(signals)

    # ── TIER 2: Fast secondary checks (~2s) ──────────────────────────────────
    # Always run tier 2 (gov + wikidata + sg_gov_live are fast)
    t2_results = await asyncio.gather(
        fetch_gov_data(req.name, lat, lon),
        fetch_wikidata(req.name, osm_id or ""),
        fetch_sg_gov_live(req.name, lat, lon),
        return_exceptions=True,
    )
    for r in t2_results:
        if isinstance(r, dict):
            signals.append(r)

    early_conf = _current_confidence(signals)

    # ── TIER 3: Slow sources — skip if already confident ─────────────────────
    # Early exit: if confidence ≥ 85% after Tier 2, skip slow sources
    if early_conf < 85:
        t3_results = await asyncio.gather(
            fetch_food_platforms(req.name, lat, lon),
            fetch_social_signals(req.name, lat, lon),
            fetch_wayback(geo.get("website")),
            fetch_tripadvisor(req.name, lat, lon),
            fetch_mapillary(lat, lon),
            return_exceptions=True,
        )
        for r in t3_results:
            if isinstance(r, dict):
                signals.append(r)

    # ── Score ────────────────────────────────────────────────────────────────
    staleness = get_staleness_context(osm_id or "", tag_type, lat, lon)
    score_result = compute_score(signals, staleness)
    confidence = score_result["confidence"]
    recommendation = score_result["recommendation"]
    predicted_status = score_result["predicted_status"]
    conflict_flag = score_result.get("conflict_flag", False)
    changeset_diff = generate_changeset_diff(signals, osm_id) if osm_found else None
    narrative = build_narrative(signals, score_result)

    confirmed_from = [
        s["source"] for s in signals
        if s.get("status") == "ACTIVE" and s.get("confidence", 0) > 0.4
    ]

    # ── Nearby places if confidence low ──────────────────────────────────────
    nearby = None
    if confidence < 50 or not osm_found:
        nearby = await fetch_nearby_places(lat, lon, tag_type, exclude_name=req.name)

    # ── Build summary string ─────────────────────────────────────────────────
    db_detail = next(
        (s["detail"] for s in signals if s.get("source") in ("gov_data", "sg_gov_live")
         and s.get("status") != "UNKNOWN"), None
    )
    if not db_detail:
        db_detail = "Found in OSM" if osm_found else "Not Found in database"

    summary = _build_summary(
        name=req.name, address=req.address, lat=lat, lon=lon,
        osm_found=osm_found, predicted_status=predicted_status,
        confidence=confidence, confirmed_from=confirmed_from,
        recommendation=recommendation, db_detail=db_detail,
    )

    source_objs = [
        SourceSignal(
            source=s.get("source", "unknown"),
            status=s.get("status", "UNKNOWN"),
            confidence=s.get("confidence", 0.0),
            detail=s.get("detail"),
            last_activity_date=s.get("last_activity_date"),
        )
        for s in signals
    ]

    result = VerifyResponse(
        summary=summary,
        place_name=req.name,
        address=req.address,
        lat=lat, lon=lon,
        osm_id=osm_id,
        osm_found=osm_found,
        predicted_status=predicted_status,
        recommendation=recommendation,
        confidence=confidence,
        sources=source_objs,
        narrative=narrative,
        conflict_flag=conflict_flag,
        confirmed_from=confirmed_from,
        edit_age_days=edit_age_days,
        neighbourhood_activity_score=staleness.get("neighbourhood_activity_score"),
        prior_p_active=staleness.get("prior_p_active"),
        changeset_diff=changeset_diff,
        nearby_places=nearby,
        osm_edit_url=f"https://www.openstreetmap.org/node/{osm_id}" if osm_id else None,
    )

    _cache_set(cache_key, result.model_dump())
    return result


# ── /search endpoint (used by frontend) ─────────────────────────────────────
@app.get("/search")
async def search(q: str):
    NOMINATIM = "https://nominatim.openstreetmap.org/search"
    OVERPASS = "https://overpass-api.de/api/interpreter"
    HEADERS = {"User-Agent": "osm-sg-validator/1.0"}
    SG_BBOX = (1.2, 103.6, 1.5, 104.0)
    candidates, lat, lon = [], None, None

    async with httpx.AsyncClient(timeout=10, headers=HEADERS) as client:
        try:
            resp = await client.get(NOMINATIM, params={
                "q": f"{q} Singapore", "format": "json",
                "countrycodes": "sg", "limit": 5,
            })
            for hit in resp.json():
                h_lat, h_lon = float(hit["lat"]), float(hit["lon"])
                if not (SG_BBOX[0] <= h_lat <= SG_BBOX[2] and SG_BBOX[1] <= h_lon <= SG_BBOX[3]):
                    continue
                if lat is None:
                    lat, lon = h_lat, h_lon
                candidates.append({
                    "osm_node_id": str(hit.get("osm_id", "")),
                    "name": hit.get("display_name", q).split(",")[0],
                    "lat": h_lat, "lon": h_lon,
                    "tags": {"type": hit.get("osm_type", "node")},
                })
        except Exception:
            pass

        if lat and len(candidates) < 3:
            try:
                oq = f"""[out:json][timeout:10];
                (node(around:300,{lat},{lon})["name"~"{q}",i];
                 way(around:300,{lat},{lon})["name"~"{q}",i];);out meta 8;"""
                r2 = await client.post(OVERPASS, data={"data": oq})
                for el in r2.json().get("elements", []):
                    tags = el.get("tags", {})
                    name = tags.get("name", "")
                    if not name:
                        continue
                    node_id = str(el.get("id", ""))
                    if any(c["osm_node_id"] == node_id for c in candidates):
                        continue
                    candidates.append({
                        "osm_node_id": node_id,
                        "name": name,
                        "lat": el.get("lat", lat),
                        "lon": el.get("lon", lon),
                        "tags": tags,
                    })
            except Exception:
                pass

    if not candidates:
        return {"error": f"No candidates found for '{q}'", "candidates": []}
    return {"query": q, "count": len(candidates), "candidates": candidates, "lat": lat, "lon": lon}


# ── /heatmap-data ────────────────────────────────────────────────────────────
@app.get("/heatmap-data")
async def heatmap_data():
    return {"nodes": HEATMAP_CACHE}


# ── /nearby ──────────────────────────────────────────────────────────────────
@app.get("/nearby")
async def nearby_endpoint(lat: float, lon: float, tag: str = "amenity", radius: int = 500):
    places = await fetch_nearby_places(lat, lon, tag_type=tag, radius_m=radius)
    return {"places": [p.model_dump() for p in places]}


# ── /submit-changeset ─────────────────────────────────────────────────────────
@app.post("/submit-changeset")
async def submit_changeset(osm_id: str, tags_after: dict):
    try:
        url = await submit_osm_changeset(osm_id, tags_after)
        return {"status": "submitted", "changeset_url": url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))