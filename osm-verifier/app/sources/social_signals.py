# app/sources/social_signals.py
import httpx
from datetime import datetime

REDDIT_SEARCH = "https://www.reddit.com/search.json"
HEADERS = {"User-Agent": "osm-sg-validator/1.0"}


async def fetch_social_signals(name: str, lat: float, lon: float) -> dict:
    """
    Query Reddit public JSON for r/singapore and r/askSingapore.
    No API key required. Extract post recency as a liveness signal.
    """
    try:
        results = []
        async with httpx.AsyncClient(timeout=8, headers=HEADERS) as client:
            for sub in ["singapore", "askSingapore", "SGFood"]:
                try:
                    resp = await client.get(REDDIT_SEARCH, params={
                        "q": f"{name} Singapore",
                        "restrict_sr": "false",
                        "sort": "new",
                        "limit": 10,
                        "subreddit": sub,
                    })
                    data = resp.json()
                    for post in data.get("data", {}).get("children", []):
                        p = post.get("data", {})
                        results.append({
                            "title": p.get("title", ""),
                            "created_utc": p.get("created_utc", 0),
                            "score": p.get("score", 0),
                            "subreddit": p.get("subreddit", ""),
                        })
                except Exception:
                    continue

        if not results:
            return {
                "source": "reddit",
                "status": "UNKNOWN",
                "confidence": 0.0,
                "detail": "No Reddit posts found for this place"
            }

        # Sort by recency
        results.sort(key=lambda x: x["created_utc"], reverse=True)
        most_recent_ts = results[0]["created_utc"]
        most_recent_dt = datetime.utcfromtimestamp(most_recent_ts)
        days_since = (datetime.utcnow() - most_recent_dt).days

        # Check for closure language in titles
        all_titles = " ".join(r["title"] for r in results).lower()
        closed_keywords = ["closed", "closing", "shut", "gone", "no more", "miss", "used to", "was here"]
        active_keywords  = ["open", "just went", "tried", "recommend", "queue", "good", "nice", "love"]

        closed_hits = sum(1 for kw in closed_keywords if kw in all_titles)
        active_hits  = sum(1 for kw in active_keywords  if kw in all_titles)

        if days_since <= 90 and active_hits > 0:
            status = "ACTIVE"
            confidence = 0.70
            detail = f"Recent Reddit post {days_since}d ago mentioning place (active language)"
        elif days_since <= 365 and closed_hits > active_hits:
            status = "CLOSED"
            confidence = 0.60
            detail = f"Reddit posts mention closure: '{results[0]['title'][:80]}'"
        elif days_since > 730:
            status = "CLOSED"
            confidence = 0.50
            detail = f"Last Reddit mention was {days_since} days ago — social flatline"
        else:
            status = "UNKNOWN"
            confidence = 0.30
            detail = f"Reddit mentions exist but inconclusive ({days_since}d ago)"

        return {
            "source": "reddit",
            "status": status,
            "confidence": confidence,
            "detail": detail,
            "last_activity_date": most_recent_dt.strftime("%Y-%m-%d"),
        }

    except Exception as e:
        return {"source": "reddit", "status": "UNKNOWN", "confidence": 0.0, "detail": str(e)}