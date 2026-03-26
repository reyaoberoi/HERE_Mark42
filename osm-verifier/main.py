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
import psycopg2
from fastapi import FastAPI
from dotenv import load_dotenv

from models import VerifyRequest, VerifyResponse, SourceResult
from app.sources.geo import get_geo_signal, geocode_nominatim, query_overpass_nearby
from app.sources.stats import get_staleness_signal, get_neighbourhood_density, load_stats
from app.sources.gov_data import check_gov_data
from app.sources.food_platforms import check_food_platforms
from app.sources.social_signals import check_social_signal
from app.scorer.weighted_scorer import compute_score, source_input_from_dict
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

load_dotenv()

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
    Geocode a query string and find nearby OSM candidates.
    """
    coords = await geocode_nominatim(q)
    if not coords:
        return {"error": "Could not find coordinates for query", "candidates": []}
    
    lat, lon = coords
    nodes = await query_overpass_nearby(lat, lon, radius=200)
    
    candidates = []
    for node in nodes:
        tags = node.get("tags", {})
        candidates.append({
            "osm_node_id": str(node.get("id")),
            "name": tags.get("name", "Unknown"),
            "lat": node.get("lat"),
            "lon": node.get("lon"),
            "tags": tags
        })
    
    return {
        "query": q,
        "lat": lat,
        "lon": lon,
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
    geo_raw, gov_raw, food_raw, social_raw = await asyncio.gather(
        get_geo_signal(name=req.name, lat=req.lat, lon=req.lon, postal_code=postal_code),
        check_gov_data(req.name, postal_code),
        check_food_platforms(req.name),
        check_social_signal(req.name),
        return_exceptions=True,
    )

    def _safe(result, source_name: str, fallback_signal: str = "unknown") -> dict:
        """Normalise exceptions to a graceful unknown-signal dict."""
        if isinstance(result, Exception):
            return {
                "source": source_name,
                "signal": fallback_signal,
                "confidence": 0.0,
                "detail": f"Source error: {result}",
            }
        return result

    geo_raw   = _safe(geo_raw,    "geo")
    gov_raw   = _safe(gov_raw,    "gov_data")
    food_raw  = _safe(food_raw,   "food_platforms")
    social_raw = _safe(social_raw, "social_signal")

    #2. Stats signal
    stats_cache = load_stats()
    edit_age = geo_raw.get("meta", {}).get("edit_age_days") if isinstance(geo_raw.get("meta"), dict) else None

    if edit_age is not None:
        stats_raw = get_staleness_signal(edit_age, tag_type, stats_cache)
    else:
        stats_raw = {
            "source": "stats",
            "signal": "unknown",
            "confidence": 0.3,
            "detail": "No edit age available from geo signal",
        }

    #3. Neighbourhood density
    density = get_neighbourhood_density(req.lat, req.lon, stats_cache)

    #4. Weighted scorer
    all_sources = [geo_raw, gov_raw, food_raw, stats_raw, social_raw]
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

    narrative = (
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