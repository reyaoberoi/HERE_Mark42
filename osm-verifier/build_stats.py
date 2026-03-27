# build_stats.py
# Run once before starting the server: python build_stats.py
# Fetches all SG POI nodes from Overpass and writes sg_nodes.json + heatmap.json

import asyncio
import json
import httpx
from datetime import datetime, timezone

OVERPASS = "https://overpass-api.de/api/interpreter"


async def fetch_sg_nodes():
    print("Fetching all SG POI nodes from Overpass (~2 min)...")
    query = """
    [out:json][timeout:180];
    (
      node["shop"](1.2,103.6,1.5,104.0);
      node["amenity"](1.2,103.6,1.5,104.0);
      node["tourism"](1.2,103.6,1.5,104.0);
      node["leisure"](1.2,103.6,1.5,104.0);
    );
    out meta;
    """
    async with httpx.AsyncClient(
        timeout=200, headers={"User-Agent": "osm-sg-validator/1.0"}
    ) as client:
        resp = await client.post(OVERPASS, data={"data": query})
        data = resp.json()

    nodes = data.get("elements", [])
    print(f"Fetched {len(nodes)} nodes")

    with open("sg_nodes.json", "w") as f:
        json.dump(nodes, f)
    print("Saved sg_nodes.json")

    # Compute heatmap scores
    now = datetime.now(timezone.utc)
    heatmap = []

    TAG_PRIORS = {"shop": 0.68, "amenity": 0.75, "tourism": 0.82, "leisure": 0.78}

    for node in nodes:
        lat = node.get("lat")
        lon = node.get("lon")
        if not lat or not lon:
            continue

        tags = node.get("tags", {})
        ts   = node.get("timestamp", "")

        try:
            edited = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            age_days = (now - edited).days
        except Exception:
            age_days = 999

        tag_type = "amenity"
        for k in ("shop", "amenity", "tourism", "leisure"):
            if k in tags:
                tag_type = k
                break

        prior = TAG_PRIORS.get(tag_type, 0.72)
        if age_days < 180:
            risk = 1.0 - prior * 0.9
        elif age_days < 365:
            risk = 1.0 - prior * 0.75
        elif age_days < 730:
            risk = 1.0 - prior * 0.50
        else:
            risk = 1.0 - prior * 0.25

        heatmap.append({
            "lat": lat,
            "lon": lon,
            "risk": round(risk, 3),
            "name": tags.get("name", ""),
            "tag_type": tag_type,
            "edit_age_days": age_days,
            "osm_id": str(node.get("id", "")),
        })

    with open("heatmap.json", "w") as f:
        json.dump(heatmap, f)
    print(f"Saved heatmap.json with {len(heatmap)} scored nodes")


if __name__ == "__main__":
    asyncio.run(fetch_sg_nodes())