# app/sources/tripadvisor.py
# Scrapes TripAdvisor's unofficial location-search JSON endpoint.
# No API key needed — uses the public typeahead endpoint.
import httpx
import re

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; osm-sg-validator/1.0)",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.tripadvisor.com/",
}


async def fetch_tripadvisor(name: str, lat: float, lon: float) -> dict:
    try:
        async with httpx.AsyncClient(timeout=8, headers=_HEADERS) as client:
            resp = await client.get(
                "https://www.tripadvisor.com/TypeAheadJson",
                params={
                    "query": name,
                    "lang": "en_US",
                    "typeaheadv2": "true",
                    "resultTypes": "eat,hotel,attr",
                    "numResults": 5,
                    "currency": "SGD",
                    "strictAnd": "false",
                },
            )
            data = {}
            if "application/json" in (resp.headers.get("content-type", "").lower()):
                data = resp.json()
            else:
                # TripAdvisor sometimes returns HTML/challenge page instead of JSON.
                txt = resp.text.lower()
                if name.lower() in txt and "singapore" in txt:
                    return {
                        "source": "tripadvisor",
                        "status": "ACTIVE",
                        "confidence": 0.35,
                        "detail": "TripAdvisor page mentions place in Singapore (fallback html match)",
                    }
                return {
                    "source": "tripadvisor",
                    "status": "UNKNOWN",
                    "confidence": 0.15,
                    "detail": "TripAdvisor endpoint returned non-json page",
                }

        results = data.get("results", []) if isinstance(data, dict) else []
        # Filter to Singapore results using lat/lon bounding box
        SG = {"lat": (1.2, 1.5), "lon": (103.6, 104.0)}
        matches = []
        for r in results:
            geo = r.get("detailLatlng", {})
            r_lat = geo.get("lat", 0.0)
            r_lon = geo.get("lng", 0.0)
            if SG["lat"][0] <= r_lat <= SG["lat"][1] and SG["lon"][0] <= r_lon <= SG["lon"][1]:
                matches.append(r)

        if not matches:
            return {"source": "tripadvisor", "status": "UNKNOWN", "confidence": 0.0,
                    "detail": "No TripAdvisor listing found in Singapore"}

        top = matches[0]
        ta_name = top.get("value", "")
        rating = top.get("rating", None)
        review_count = int(top.get("reviewCount", 0) or 0)

        # A listing with recent reviews is a strong liveness signal
        if review_count and int(review_count) > 10:
            conf = min(0.5 + int(review_count) / 2000, 0.92)
            return {
                "source": "tripadvisor",
                "status": "ACTIVE",
                "confidence": round(conf, 3),
                "detail": f"TripAdvisor: '{ta_name}', {review_count} reviews, rating={rating}",
            }
        elif review_count:
            return {
                "source": "tripadvisor",
                "status": "ACTIVE",
                "confidence": 0.45,
                "detail": f"TripAdvisor: '{ta_name}', {review_count} reviews",
            }
        else:
            title_blob = f"{top.get('value', '')} {top.get('secondaryText', '')}"
            if name.lower() in title_blob.lower() and "singapore" in title_blob.lower():
                return {
                    "source": "tripadvisor",
                    "status": "ACTIVE",
                    "confidence": 0.35,
                    "detail": f"TripAdvisor: listing found for '{ta_name}' in Singapore",
                }
            return {
                "source": "tripadvisor",
                "status": "UNKNOWN",
                "confidence": 0.2,
                "detail": f"TripAdvisor: listing exists but no review count",
            }

    except Exception as e:
        return {"source": "tripadvisor", "status": "UNKNOWN", "confidence": 0.0, "detail": str(e)}
