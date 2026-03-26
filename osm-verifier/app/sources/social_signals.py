"""
S5b — Reddit SG + DuckDuckGo Social Signal
Person B owns this file.
Sources: r/singapore + r/askSingapore public JSON (no API key)
         DuckDuckGo Instant Answer social presence check
"""

import asyncio
import aiohttp
from datetime import datetime, timezone


SUBREDDITS = ["singapore", "askSingapore"]
REDDIT_BASE = "https://www.reddit.com/r/{sub}/search.json"
DDG_URL = "https://api.duckduckgo.com/"
TIMEOUT = aiohttp.ClientTimeout(total=6)
HEADERS = {"User-Agent": "osm-sg-validator/1.0 (research project)"}


async def _search_reddit(session: aiohttp.ClientSession, name: str, subreddit: str) -> list[dict]:
    """Search one subreddit, return list of post dicts with date + title."""
    url = REDDIT_BASE.format(sub=subreddit)
    params = {"q": f"{name} Singapore", "sort": "new", "limit": 10, "restrict_sr": 1, "t": "all"}
    try:
        async with session.get(url, params=params, headers=HEADERS, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            posts = data.get("data", {}).get("children", [])
            results = []
            for p in posts:
                d = p.get("data", {})
                created_utc = d.get("created_utc")
                title = d.get("title", "")
                if created_utc:
                    dt = datetime.fromtimestamp(created_utc, tz=timezone.utc)
                    results.append({
                        "title": title,
                        "date": dt.strftime("%Y-%m-%d"),
                        "subreddit": subreddit,
                        "url": f"https://reddit.com{d.get('permalink', '')}",
                    })
            return results
    except Exception as e:
        print(f"[reddit] {subreddit} error: {e}")
        return []


async def _search_duckduckgo(session: aiohttp.ClientSession, name: str) -> dict:
    """DuckDuckGo Instant Answer for social presence."""
    params = {"q": f"{name} Singapore", "format": "json", "no_html": 1, "skip_disambig": 1}
    try:
        async with session.get(DDG_URL, params=params, headers=HEADERS, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                return {"ddg_found": False, "abstract": None}
            data = await resp.json(content_type=None)
            abstract = data.get("Abstract") or data.get("Answer") or ""
            related = data.get("RelatedTopics", [])
            found = bool(abstract) or any(name.lower() in str(r).lower() for r in related[:5])
            return {"ddg_found": found, "abstract": abstract[:200] if abstract else None}
    except Exception as e:
        print(f"[duckduckgo] Error: {e}")
        return {"ddg_found": False, "abstract": None}


def _compute_recency_score(posts: list[dict]) -> float:
    """
    Score 0.0-1.0 based on how recent the Reddit posts are.
    Recent post (< 3 months) = high score. Older = lower.
    No posts = 0.0
    """
    if not posts:
        return 0.0

    now = datetime.now(tz=timezone.utc)
    scores = []
    for post in posts:
        try:
            dt = datetime.strptime(post["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            age_days = (now - dt).days
            if age_days < 90:
                scores.append(1.0)
            elif age_days < 180:
                scores.append(0.8)
            elif age_days < 365:
                scores.append(0.5)
            elif age_days < 730:
                scores.append(0.3)
            else:
                scores.append(0.1)
        except Exception:
            continue

    return round(max(scores) if scores else 0.0, 2)


def _detect_closure_mentions(posts: list[dict]) -> bool:
    """Scan post titles for closure language."""
    closure_keywords = [
        "closed", "close down", "closing", "shut", "shutting", "no longer",
        "permanent", "gone", "moved away", "not there", "lah closed",
    ]
    for post in posts:
        title_lower = post["title"].lower()
        if any(kw in title_lower for kw in closure_keywords):
            return True
    return False


async def check_social_signal(name: str) -> dict:
    """
    Main entry point. Runs Reddit (both subreddits) + DDG in parallel.
    Returns signal dict.
    """
    try:
        async with aiohttp.ClientSession() as session:
            tasks = [_search_reddit(session, name, sub) for sub in SUBREDDITS]
            tasks.append(_search_duckduckgo(session, name))

            results = await asyncio.gather(*tasks, return_exceptions=True)

        # Unpack results safely
        reddit_posts = []
        for i, r in enumerate(results[:-1]):
            if isinstance(r, list):
                reddit_posts.extend(r)

        ddg = results[-1] if not isinstance(results[-1], Exception) else {"ddg_found": False, "abstract": None}

        # Compute signals
        recency_score = _compute_recency_score(reddit_posts)
        closure_mentioned = _detect_closure_mentions(reddit_posts)
        latest_post = max((p["date"] for p in reddit_posts), default=None)

        # Determine signal
        if closure_mentioned:
            signal = "closed"
            confidence = 0.70
        elif recency_score >= 0.3:
            signal = "active"
            # Base confidence from recency, boosted by volume of posts
            volume_boost = min(0.15, len(reddit_posts) * 0.015)
            confidence = min(0.95, recency_score * 0.7 + volume_boost)
        elif reddit_posts:
            signal = "active"  # Any posts at all = weak active signal
            confidence = 0.35
        else:
            signal = "unknown"
            confidence = 0.1

        detail_parts = []
        if reddit_posts:
            detail_parts.append(f"{len(reddit_posts)} Reddit posts, latest {latest_post}")
        if closure_mentioned:
            detail_parts.append("closure language detected")
        if ddg["ddg_found"]:
            detail_parts.append("DDG social presence found")
            # DDG presence boosts confidence by 5%
            if signal == "active":
                confidence = min(0.95, confidence + 0.05)

        return {
            "source": "social_signal",
            "signal": signal,
            "confidence": round(confidence, 2),
            "reddit_recency_score": recency_score,
            "reddit_post_count": len(reddit_posts),
            "latest_post_date": latest_post,
            "closure_mentioned": closure_mentioned,
            "social_active": ddg["ddg_found"],
            "detail": " | ".join(detail_parts) if detail_parts else "No social signal found",
            "posts": reddit_posts[:5],  # top 5 for audit log
        }

    except Exception as e:
        return {
            "source": "social_signal",
            "signal": "unknown",
            "confidence": 0.0,
            "detail": f"Error: {str(e)}",
        }


# ── Quick test ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    result = asyncio.run(check_social_signal("Lau Pa Sat"))
    import json
    print(json.dumps(result, indent=2))