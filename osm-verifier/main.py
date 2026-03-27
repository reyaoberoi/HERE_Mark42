"""
OSM Verifier — main entry point.
Aggregates all 5 signal sources and fuses them via the weighted scorer.

Sources
-------
  geo            — OSM Overpass + edit-age + contributor trust   (weight 0.25)
  gov_data       — NEA/STB licences + Wikidata dissolved flag    (weight 0.30)
  food_platforms — Burpple / HungryGoWhere live scrape           (weight 0.25)
  stats          — population-level staleness percentile         (weight 0.10)
  social_signal  — Reddit SG + DuckDuckGo social presence       (weight 0.10)

Run with:
  uvicorn main:app --reload
"""

import os
import asyncio
import httpx
import psycopg2
from fastapi import FastAPI, Query, HTTPException
from dotenv import load_dotenv

from models import VerifyRequest, VerifyResponse, SourceResult
from app.sources.geo import get_geo_signal, geocode_nominatim, query_overpass_nearby, geocode_onemap
from app.sources.stats import get_staleness_signal, get_neighbourhood_density, load_stats
from app.sources.gov_data import check_gov_data
from app.sources.wikidata import check_wikidata
from app.sources.food_platforms import check_food_platforms
from app.sources.social_signals import check_social_signal
from app.scorer.weighted_scorer import compute_score, source_input_from_dict
import logging
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
import folium

def generate_map(lat, lon, result):
    m = folium.Map(location=[lat, lon], zoom_start=18)
    color = "blue"
    if result.get("recommendation") == "ACCEPT": color = "green"
    elif result.get("recommendation") == "REJECT": color = "red"
    elif result.get("recommendation") == "REVIEW": color = "orange"
    
    folium.Marker(
        [lat, lon],
        popup=f"<b>{result.get('osm_node_id', 'Unknown')}</b><br>{result.get('recommendation', '')}",
        tooltip=result.get('recommendation', 'POI'),
        icon=folium.Icon(color=color)
    ).add_to(m)
    return m.get_root().render()

load_dotenv()

# ── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    force=True
)
logger = logging.getLogger("api")

app = FastAPI(title="OSM Verifier API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

#Frontend Mount
app.mount("/static", StaticFiles(directory="../frontend"), name="static")

@app.get("/", response_class=HTMLResponse)
@app.get("/tester", response_class=HTMLResponse)
async def get_tester():
    with open("../frontend/index.html", "r") as f:
        return f.read()


#Health check
@app.get("/health")
def health():
    db_status = "Disconnected"
    db_error = None
    row_count = 0
    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM raw_osm_data")
            row_count = cur.fetchone()[0]
        conn.close()
        db_status = "Connected"
    except Exception as e:
        db_error = str(e)

    return {
        "status": "ok",
        "database": db_status,
        "row_count": row_count,
        "error": db_error,
        "connection_string": os.getenv("DATABASE_URL"),
    }


#Searh
@app.get("/search")
async def search(q: str):
    """
    Search for POIs. Tries local PostGIS first (fuzzy), then falls back to Nominatim/Overpass.
    """
    logger.info(f"Searching for: {q}")
    candidates = []

    # ── 1. Local Search (PostGIS) ───────────────────────────────────────────
    # Singapore bounding box filter
    SG_MIN_LAT, SG_MAX_LAT = 1.1304, 1.4784
    SG_MIN_LON, SG_MAX_LON = 103.6065, 104.0860

    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        with conn.cursor() as cur:
            # Fuzzy search filtered to Singapore bbox only
            query = """
                SELECT osm_id, name, ST_Y(geom) as lat, ST_X(geom) as lon, all_tags
                FROM (
                    SELECT osm_id, name, geom, 
                           (to_jsonb(t.*) - 'osm_id' - 'name' - 'geom') as all_tags
                    FROM raw_osm_data t
                    WHERE ST_Y(geom) BETWEEN %s AND %s
                      AND ST_X(geom) BETWEEN %s AND %s
                ) sub
                WHERE name ILIKE %s 
                OR name %% %s
                OR (all_tags->>'addr:street') ILIKE %s
                OR (all_tags->>'addr:postcode') ILIKE %s
                ORDER BY GREATEST(
                    similarity(COALESCE(name, ''), %s),
                    similarity(COALESCE(all_tags->>'addr:street', ''), %s)
                ) DESC
                LIMIT 10
            """
            cur.execute(query, (SG_MIN_LAT, SG_MAX_LAT, SG_MIN_LON, SG_MAX_LON,
                                f"%{q}%", q, f"%{q}%", f"{q}%", q, q))
            rows = cur.fetchall()
            for r in rows:
                candidates.append({
                    "osm_node_id": str(r[0]),
                    "name": r[1],
                    "lat": r[2],
                    "lon": r[3],
                    "tags": r[4] or {}
                })
        conn.close()
        logger.info(f"Local search found {len(candidates)} candidates")
    except Exception as e:
        logger.error(f"Local search failed: {e}")

    # ── 2. Fallback to Nominatim/Overpass if few local results ──────────────
    if len(candidates) < 3:
        logger.info("Fewer than 3 local results. Trying external OSM search (OneMap/Nominatim)...")
        coords = await geocode_onemap(q)
        if not coords:
            coords = await geocode_nominatim(q)
        if coords:
            lat, lon = coords
            nodes = await query_overpass_nearby(lat, lon, radius=500)
            for node in nodes:
                node_id = str(node.get("id"))
                # Avoid duplicates
                if not any(c["osm_node_id"] == node_id for c in candidates):
                    tags = node.get("tags", {})
                    candidates.append({
                        "osm_node_id": node_id,
                        "name": tags.get("name", "Unknown"),
                        "lat": node.get("lat"),
                        "lon": node.get("lon"),
                        "tags": tags
                    })
            logger.info(f"External search added {len(candidates)} candidates total")

    if not candidates:
        return {"error": f"No candidates found for '{q}'", "candidates": []}

    return {
        "query": q,
        "count": len(candidates),
        "candidates": candidates
    }


#Verify endpoint
@app.post("/verify", response_model=VerifyResponse)
async def verify(req: VerifyRequest):
    """
    Run all 5 signal sources concurrently and fuse results using the
    confidence-weighted scorer.
    """
    postal_code = req.tags.get("postal_code", "") or req.tags.get("addr:postcode", "")
    tag_type = next(
        (req.tags.get(k) for k in ["amenity", "shop", "tourism", "leisure"] if req.tags.get(k)),
        None,
    )

    #1. Run geo + three external sources concurrently
    logger.info(f"Initiating verification for node {req.osm_node_id} ({req.name})")
    
    geo_raw, gov_raw, wiki_raw, food_raw, social_raw = await asyncio.gather(
        get_geo_signal(name=req.name, lat=req.lat, lon=req.lon, postal_code=postal_code),
        check_gov_data(req.name, postal_code),
        check_wikidata(req.name),
        check_food_platforms(req.name),
        check_social_signal(req.name),
        return_exceptions=True,
    )

    def _safe(result, source_name: str, fallback_signal: str = "unknown") -> dict:
        """Normalise exceptions to a graceful unknown-signal dict."""
        if isinstance(result, Exception):
            logger.error(f"Source {source_name} failed: {result}")
            return {
                "source": source_name,
                "signal": fallback_signal,
                "confidence": 0.0,
                "detail": f"Source error: {result}",
            }
        logger.info(f"Source {source_name} completed with signal: {result.get('signal')}")
        return result

    geo_raw    = _safe(geo_raw,    "geo")
    gov_raw    = _safe(gov_raw,    "gov_data")
    wiki_raw   = _safe(wiki_raw,   "wikidata")
    food_raw   = _safe(food_raw,   "food_platforms")
    social_raw = _safe(social_raw, "social_signal")

    #2. Stats signal — try geo meta first, fallback to direct OSM node lookup
    stats_cache = load_stats()
    edit_age = geo_raw.get("meta", {}).get("edit_age_days") if isinstance(geo_raw.get("meta"), dict) else None

    # If geo didn't provide edit age, try direct OSM node lookup
    if edit_age is None and req.osm_node_id:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                osm_resp = await client.get(
                    f"https://api.openstreetmap.org/api/0.6/node/{req.osm_node_id}.json",
                    headers={"User-Agent": "osm-verifier/1.0"}
                )
                if osm_resp.status_code == 200:
                    node_data = osm_resp.json().get("elements", [{}])[0]
                    timestamp = node_data.get("timestamp")
                    if timestamp:
                        from datetime import datetime, timezone
                        edited_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                        edit_age = (datetime.now(timezone.utc) - edited_at).days
                        logger.info(f"Direct OSM lookup: node {req.osm_node_id} last edited {edit_age} days ago")
        except Exception as e:
            logger.warning(f"Direct OSM node lookup failed: {e}")

    if edit_age is not None:
        stats_raw = get_staleness_signal(edit_age, tag_type, stats_cache)
    else:
        stats_raw = {
            "source": "stats",
            "signal": "unknown",
            "confidence": 0.1,
            "detail": "No edit age available",
        }

    #3. Neighbourhood density
    density = get_neighbourhood_density(req.lat, req.lon, stats_cache)

    #4. Weighted scorer
    all_sources = [geo_raw, gov_raw, wiki_raw, food_raw, stats_raw, social_raw]
    scorer_inputs = [source_input_from_dict(s) for s in all_sources]
    result = compute_score(scorer_inputs)

    #5. Build response
    source_results = [
        SourceResult(
            source=b["source"],
            signal=b["signal"],
            confidence=b["source_confidence"],
            detail=b.get("detail"),
        )
        for b in result.source_breakdown
    ]

    # --- BASELINE UPDATE & COMPARE ---
    baseline_info = "Baseline DB Sync: Not found locally"
    try:
        if req.osm_node_id:
            conn = psycopg2.connect(os.getenv("DATABASE_URL"))
            with conn.cursor() as cur:
                # Ensure the tracking column exists
                cur.execute("ALTER TABLE raw_osm_data ADD COLUMN IF NOT EXISTS verified_status VARCHAR DEFAULT 'unverified';")
                conn.commit()
                
                # Check current baseline status
                # ogr2ogr creates osm_id as varchar or integer depending on config, we cast safely.
                cur.execute("SELECT verified_status FROM raw_osm_data WHERE osm_id::text = %s", (str(req.osm_node_id),))
                row = cur.fetchone()
                if row:
                    prev_status = row[0] or "unverified"
                    
                    # Update baseline
                    new_status = "closed" if result.recommendation == "REJECT" else ("active" if result.recommendation == "ACCEPT" else prev_status)
                    if new_status != prev_status:
                        cur.execute("UPDATE raw_osm_data SET verified_status = %s WHERE osm_id::text = %s", (new_status, str(req.osm_node_id)))
                        conn.commit()
                        baseline_info = f"Baseline DB Sync: Updated from '{prev_status}' to '{new_status}'"
                    else:
                        baseline_info = f"Baseline DB Sync: Remains '{prev_status}'"
            conn.close()
    except Exception as e:
        logger.error(f"Baseline update failed: {e}")
        baseline_info = f"Baseline DB Sync: Error ({e})"

    narrative = (
        f"[{baseline_info}]\n"
        f"{result.narrative} | "
        f"Area density: {density} nodes/500m cell"
    )

    return VerifyResponse(
        osm_node_id=req.osm_node_id,
        confidence=result.confidence,
        recommendation=result.recommendation,
        sources=source_results,
        narrative=narrative,
        before_image_url=None,
        after_image_url=None,
        changeset_diff={
            "weighted_score": result.weighted_score,
            "source_breakdown": result.source_breakdown,
            "unknown_sources": result.unknown_sources,
            "neighbourhood_density": density,
        },
    )


@app.get("/map", response_class=HTMLResponse)
async def get_map(
    lat: float = Query(..., description="Latitude"),
    lon: float = Query(..., description="Longitude"),
    name: str = Query(None, description="Location name"),
    osm_node_id: str = Query(..., description="OSM Node ID")
):
    try:
        geo_result = await get_geo_signal(name=name or "Unknown", lat=lat, lon=lon, postal_code="")

        stats_cache = load_stats()
        edit_age = (geo_result.get("meta") or {}).get("edit_age_days")
        stats_result = get_staleness_signal(edit_age, None, stats_cache) if edit_age else {
            "source": "stats", "signal": "unknown", "confidence": 0.1, "detail": "No edit age"
        }

        result = {
            "osm_node_id": osm_node_id,
            "confidence": geo_result.get("confidence", 0),
            "recommendation": geo_result.get("recommendation", "REVIEW"),
            "sources": [
                {"source": "geo", "signal": geo_result.get("signal"),
                 "confidence": geo_result.get("confidence"), "detail": geo_result.get("detail")},
                {"source": "stats", "signal": stats_result.get("signal"),
                 "confidence": stats_result.get("confidence"), "detail": stats_result.get("detail")}
            ],
            "narrative": f"Geo: {geo_result.get('detail')} | Stats: {stats_result.get('detail')}"
        }

        map_html = generate_map(lat, lon, result)
        return map_html

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))