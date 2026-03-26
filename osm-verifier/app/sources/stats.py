import httpx
import json
import os
import numpy as np
from datetime import datetime, timezone
from typing import Optional

STATS_CACHE_PATH = "sg_stats.json"

# ─────────────────────────────────────────
# 1. Fetch full SG OSM dump (~50k nodes)
# ─────────────────────────────────────────

async def fetch_sg_osm_dump() -> list:
    print("[stats] fetching full SG OSM dump — this takes ~2 min...")
    query = """
    [out:json][timeout:180];
    (
      node["name"](1.1304,103.6065,1.4784,104.0860);
    );
    out meta;
    """
    url = "https://overpass-api.de/api/interpreter"

    async with httpx.AsyncClient(timeout=200) as client:
        try:
            r = await client.post(url, data={"data": query})
            data = r.json()
            nodes = data.get("elements", [])
            print(f"[stats] fetched {len(nodes)} nodes")
            return nodes
        except Exception as e:
            print(f"[stats] dump fetch error: {e}")
            return []

# ─────────────────────────────────────────
# 2. Compute edit age in days for a node
# ─────────────────────────────────────────

def node_edit_age_days(node: dict) -> Optional[float]:
    ts = node.get("timestamp")
    if not ts:
        return None
    try:
        edited_at = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - edited_at).days
    except Exception:
        return None

# ─────────────────────────────────────────
# 3. Compute stats and bake to sg_stats.json
# ─────────────────────────────────────────

async def build_stats_cache():
    if os.path.exists(STATS_CACHE_PATH):
        print("[stats] sg_stats.json already exists — skipping rebuild")
        return

    nodes = await fetch_sg_osm_dump()
    if not nodes:
        print("[stats] no nodes fetched — aborting")
        return

    # --- Edit age per tag type ---
    tag_ages: dict[str, list] = {}
    all_ages = []

    for node in nodes:
        age = node_edit_age_days(node)
        if age is None:
            continue
        all_ages.append(age)
        tags = node.get("tags", {})
        for key in ["amenity", "shop", "tourism", "leisure", "office"]:
            val = tags.get(key)
            if val:
                tag_ages.setdefault(val, []).append(age)

    # Median edit age per tag type
    median_by_tag = {
        tag: float(np.median(ages))
        for tag, ages in tag_ages.items()
        if len(ages) >= 3
    }

    # Global percentiles for staleness scoring
    global_percentiles = {
        "p25": float(np.percentile(all_ages, 25)),
        "p50": float(np.percentile(all_ages, 50)),
        "p75": float(np.percentile(all_ages, 75)),
        "p90": float(np.percentile(all_ages, 90)),
        "p95": float(np.percentile(all_ages, 95)),
    }

    # --- 500m neighbourhood edit density grid ---
    # Bin Singapore into ~500m cells (roughly 0.0045 degrees)
    CELL = 0.0045
    density_grid: dict[str, int] = {}

    for node in nodes:
        lat = node.get("lat")
        lon = node.get("lon")
        if lat is None or lon is None:
            continue
        cell_lat = round(round(lat / CELL) * CELL, 6)
        cell_lon = round(round(lon / CELL) * CELL, 6)
        key = f"{cell_lat},{cell_lon}"
        density_grid[key] = density_grid.get(key, 0) + 1

    # Prior P(active) — ratio of nodes edited within 2 years
    two_years = 730
    active_count = sum(1 for a in all_ages if a <= two_years)
    prior_p_active = active_count / len(all_ages) if all_ages else 0.5

    stats = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "total_nodes": len(nodes),
        "global_percentiles": global_percentiles,
        "median_edit_age_by_tag": median_by_tag,
        "density_grid": density_grid,
        "prior_p_active": prior_p_active,
    }

    with open(STATS_CACHE_PATH, "w") as f:
        json.dump(stats, f)

    print(f"[stats] sg_stats.json written — {len(nodes)} nodes, "
          f"prior_p_active={prior_p_active:.2f}")

# ─────────────────────────────────────────
# 4. Load stats from cache
# ─────────────────────────────────────────

def load_stats() -> Optional[dict]:
    if not os.path.exists(STATS_CACHE_PATH):
        return None
    with open(STATS_CACHE_PATH) as f:
        return json.load(f)

# ─────────────────────────────────────────
# 5. Staleness percentile scorer
# ─────────────────────────────────────────

def get_staleness_signal(
    edit_age_days: float,
    tag_type: Optional[str] = None,
    stats: Optional[dict] = None
) -> dict:

    if stats is None:
        stats = load_stats()
    if stats is None:
        return {
            "source": "stats",
            "signal": "unknown",
            "confidence": 0.3,
            "detail": "Stats cache not built yet"
        }

    percentiles = stats["global_percentiles"]
    median_by_tag = stats.get("median_edit_age_by_tag", {})

    # Tag-specific median if available
    tag_median = median_by_tag.get(tag_type) if tag_type else None
    reference = tag_median if tag_median else percentiles["p50"]

    # Staleness score — how old is this node vs the population
    if edit_age_days <= percentiles["p25"]:
        signal = "active"
        confidence = 0.75
        detail = f"Recently edited — top 25% freshest nodes (age {edit_age_days:.0f}d)"
    elif edit_age_days <= percentiles["p50"]:
        signal = "active"
        confidence = 0.60
        detail = f"Moderately fresh (age {edit_age_days:.0f}d, median {reference:.0f}d)"
    elif edit_age_days <= percentiles["p75"]:
        signal = "unknown"
        confidence = 0.45
        detail = f"Somewhat stale (age {edit_age_days:.0f}d, 75th pct {percentiles['p75']:.0f}d)"
    elif edit_age_days <= percentiles["p90"]:
        signal = "unknown"
        confidence = 0.35
        detail = f"Stale (age {edit_age_days:.0f}d, 90th pct {percentiles['p90']:.0f}d)"
    else:
        signal = "closed"
        confidence = 0.60
        detail = f"Very stale — bottom 10% oldest nodes (age {edit_age_days:.0f}d)"

    return {
        "source": "stats",
        "signal": signal,
        "confidence": confidence,
        "detail": detail,
        "meta": {
            "edit_age_days": edit_age_days,
            "tag_median": tag_median,
            "global_p50": percentiles["p50"],
            "prior_p_active": stats.get("prior_p_active"),
        }
    }

# ─────────────────────────────────────────
# 6. Neighbourhood density lookup
# ─────────────────────────────────────────

def get_neighbourhood_density(lat: float, lon: float, stats: Optional[dict] = None) -> int:
    if stats is None:
        stats = load_stats()
    if stats is None:
        return 0
    CELL = 0.0045
    cell_lat = round(round(lat / CELL) * CELL, 6)
    cell_lon = round(round(lon / CELL) * CELL, 6)
    key = f"{cell_lat},{cell_lon}"
    return stats.get("density_grid", {}).get(key, 0)