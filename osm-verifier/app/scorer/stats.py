# app/scorer/stats.py
import json
from collections import defaultdict

_stats_cache = None


def _load_stats():
    global _stats_cache
    if _stats_cache is not None:
        return _stats_cache
    try:
        with open("sg_nodes.json", "r") as f:
            nodes = json.load(f)
        _stats_cache = _precompute(nodes)
    except FileNotFoundError:
        _stats_cache = {"medians": {}, "grid": {}}
    return _stats_cache


def _precompute(nodes: list) -> dict:
    """
    Precompute:
    1. Median edit age per tag type (shop, amenity, tourism, leisure)
    2. Edit density per 500m grid cell (keyed by rounded lat/lon)
    """
    from datetime import datetime, timezone

    tag_ages = defaultdict(list)
    grid_counts = defaultdict(int)
    now = datetime.now(timezone.utc)

    for node in nodes:
        tags = node.get("tags", {})
        ts   = node.get("timestamp", "")
        lat  = node.get("lat")
        lon  = node.get("lon")

        if not lat or not lon or not ts:
            continue

        try:
            edited = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            age_days = (now - edited).days
        except Exception:
            continue

        for key in ("shop", "amenity", "tourism", "leisure"):
            if key in tags:
                tag_ages[key].append(age_days)
                break

        # Grid cell (approx 500m in SG latitude)
        grid_key = (round(lat * 200) / 200, round(lon * 200) / 200)
        grid_counts[grid_key] += 1

    medians = {}
    for tag_type, ages in tag_ages.items():
        ages.sort()
        mid = len(ages) // 2
        medians[tag_type] = ages[mid] if ages else 365

    return {"medians": medians, "grid": dict(grid_counts)}


def get_staleness_context(osm_id: str, tag_type: str, lat: float, lon: float) -> dict:
    """Return staleness context for a given node using precomputed sg_nodes statistics."""
    stats = _load_stats()
    medians = stats.get("medians", {})
    grid = stats.get("grid", {})

    grid_key = (round(lat * 200) / 200, round(lon * 200) / 200)
    neighbourhood_count = grid.get(grid_key, 0)
    neighbourhood_score = min(1.0, neighbourhood_count / 50.0)

    median_age = medians.get(tag_type or "amenity", 365)

    tag_priors = {
        "shop": 0.68,
        "amenity": 0.75,
        "tourism": 0.82,
        "leisure": 0.78,
    }
    prior = tag_priors.get(tag_type or "amenity", 0.72)

    return {
        "prior_p_active": prior,
        "median_edit_age_for_tag": median_age,
        "neighbourhood_activity_score": round(neighbourhood_score, 3),
        "staleness_percentile": None,
    }


def compute_staleness_percentile(edit_age_days: int, tag_type: str) -> float:
    """Given a node's edit age, return what percentile it falls in for its tag type."""
    # Placeholder — replace with actual percentile lookup after build_stats.py runs
    return 0.5
