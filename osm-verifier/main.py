from fastapi import FastAPI
from app.models import VerifyRequest, VerifyResponse, SourceResult
from app.sources.geo import get_geo_signal
from app.sources.stats import get_staleness_signal, get_neighbourhood_density, load_stats
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="OSM Verifier API")

@app.get("/")
def root():
    return {"status": "ok"}

@app.post("/verify", response_model=VerifyResponse)
async def verify(req: VerifyRequest):

    # Run geo signal
    geo = await get_geo_signal(
        name=req.name,
        lat=req.lat,
        lon=req.lon,
        postal_code=req.tags.get("postal_code", "")
    )

    # Run stats signal using edit age from geo
    stats_cache = load_stats()
    edit_age = geo.get("meta", {}).get("edit_age_days")
    tag_type = next(
        (req.tags.get(k) for k in ["amenity", "shop", "tourism", "leisure"] 
         if req.tags.get(k)), None
    )

    if edit_age is not None:
        stats_sig = get_staleness_signal(edit_age, tag_type, stats_cache)
    else:
        stats_sig = {
            "source": "stats",
            "signal": "unknown",
            "confidence": 0.3,
            "detail": "No edit age available from geo"
        }

    # Neighbourhood density
    density = get_neighbourhood_density(req.lat, req.lon, stats_cache)

    sources = [
        SourceResult(
            source=geo["source"],
            signal=geo["signal"],
            confidence=geo["confidence"],
            detail=geo.get("detail")
        ),
        SourceResult(
            source=stats_sig["source"],
            signal=stats_sig["signal"],
            confidence=stats_sig["confidence"],
            detail=stats_sig.get("detail")
        ),
    ]

    # Combined confidence — average of both signals for now
    # S7 Bayesian scorer replaces this properly later
    avg_confidence = round(
        (geo["confidence"] + stats_sig["confidence"]) / 2, 3
    )

    signals = [geo["signal"], stats_sig["signal"]]
    if signals.count("closed") >= 1:
        recommendation = "REJECT"
    elif signals.count("active") >= 1:
        recommendation = "ACCEPT"
    else:
        recommendation = "REVIEW"

    narrative = (
        f"Geo: {geo.get('detail')} | "
        f"Stats: {stats_sig.get('detail')} | "
        f"Area density: {density} nodes/500m cell"
    )

    return VerifyResponse(
        osm_node_id=req.osm_node_id,
        confidence=avg_confidence,
        recommendation=recommendation,
        sources=sources,
        narrative=narrative,
        before_image_url=None,
        after_image_url=None,
        changeset_diff=None,
    )