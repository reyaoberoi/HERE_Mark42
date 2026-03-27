# app/sources/food_platforms.py
import asyncio
import os
import re
from datetime import datetime
from typing import Optional
import httpx

try:
    from brave import AsyncBrave
    BRAVE_WRAPPER_OK = True
except Exception:
    BRAVE_WRAPPER_OK = False

# Try to import playwright — degrade gracefully if not installed
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_OK = True
except ImportError:
    PLAYWRIGHT_OK = False

try:
    import dateparser
    DATEPARSER_OK = True
except ImportError:
    DATEPARSER_OK = False


async def fetch_food_platforms(name: str, lat: float, lon: float) -> dict:
    tasks = [
        _scrape_burpple(name),
        _scrape_hungrygowhere(name),
        _brave_signal(name),
        _qwant_signal(name),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    burpple, hungry, brave, qwant = [r if isinstance(r, dict) else {} for r in results]

    # Merge: prefer the most recent date and most definitive status
    all_dates = [
        burpple.get("last_date"),
        hungry.get("last_date"),
        brave.get("last_date"),
        qwant.get("last_date"),
    ]
    all_dates = [d for d in all_dates if d]

    closed_signals = [
        burpple.get("closed", False),
        hungry.get("closed", False),
        brave.get("closed", False),
        qwant.get("closed", False),
    ]

    found_anywhere = any([burpple.get("found"), hungry.get("found"), brave.get("found"), qwant.get("found")])

    if not found_anywhere:
        return {
            "source": "food_platforms",
            "status": "UNKNOWN",
            "confidence": 0.0,
            "detail": f"'{name}' not found on Burpple, HungryGoWhere, Brave, or Qwant"
        }

    # If any source says closed definitively
    if any(closed_signals):
        return {
            "source": "food_platforms",
            "status": "CLOSED",
            "confidence": 0.80,
            "last_activity_date": min(all_dates) if all_dates else None,
            "detail": "Closed badge detected on food platform listing"
        }

    # Check recency of last activity
    if all_dates:
        most_recent_str = max(all_dates)
        try:
            if DATEPARSER_OK:
                most_recent = dateparser.parse(most_recent_str)
            else:
                most_recent = None
            days_since = (datetime.now() - most_recent).days if most_recent else 9999
        except Exception:
            days_since = 9999

        if days_since > 730:   # 2+ years, strong closure signal
            return {
                "source": "food_platforms",
                "status": "CLOSED",
                "confidence": 0.65,
                "last_activity_date": most_recent_str,
                "detail": f"Last review {days_since} days ago — review flatline detected"
            }
        elif days_since <= 180:
            return {
                "source": "food_platforms",
                "status": "ACTIVE",
                "confidence": 0.80,
                "last_activity_date": most_recent_str,
                "detail": f"Recent activity {days_since} days ago"
            }
        else:
            return {
                "source": "food_platforms",
                "status": "UNKNOWN",
                "confidence": 0.40,
                "last_activity_date": most_recent_str,
                "detail": f"Last activity {days_since} days ago — inconclusive"
            }

    return {
        "source": "food_platforms",
        "status": "ACTIVE",
        "confidence": 0.50,
        "detail": "Found on food platform but no date data"
    }


async def _scrape_burpple(name: str) -> dict:
    if not PLAYWRIGHT_OK:
        return {"found": False, "closed": False, "last_date": None}
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            ctx = await browser.new_context(user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ))
            page = await ctx.new_page()
            url = f"https://www.burpple.com/search/food?q={name.replace(' ', '+')}&loc=Singapore"
            await page.goto(url, timeout=12000, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)

            content = await page.content()
            await browser.close()

            # Detect closed badge
            closed = bool(re.search(r"permanently.closed|closed.down|no.longer.operating",
                                     content, re.IGNORECASE))

            # Extract last review date
            date_patterns = [
                r"\d+\s+(?:day|week|month|year)s?\s+ago",
                r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}",
                r"\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}",
            ]
            last_date = None
            for pat in date_patterns:
                m = re.search(pat, content, re.IGNORECASE)
                if m:
                    last_date = m.group(0)
                    break

            found = name.lower() in content.lower() or bool(last_date)
            return {"found": found, "closed": closed, "last_date": last_date}

    except Exception as e:
        return {"found": False, "closed": False, "last_date": None, "_error": str(e)}


async def _scrape_hungrygowhere(name: str) -> dict:
    """HungryGoWhere via httpx (lighter than Playwright)."""
    try:
        async with httpx.AsyncClient(timeout=8, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        }) as client:
            url = f"https://www.hungrygowhere.com/search/?keyword={name.replace(' ', '+')}"
            resp = await client.get(url, follow_redirects=True)
            content = resp.text

            closed = bool(re.search(r"permanently.closed|closed.down", content, re.IGNORECASE))
            found = name.lower() in content.lower()

            date_patterns = [
                r"\d+\s+(?:day|week|month|year)s?\s+ago",
                r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}",
            ]
            last_date = None
            for pat in date_patterns:
                m = re.search(pat, content, re.IGNORECASE)
                if m:
                    last_date = m.group(0)
                    break

            return {"found": found, "closed": closed, "last_date": last_date}
    except Exception:
        return {"found": False, "closed": False, "last_date": None}


async def _brave_signal(name: str) -> dict:
    api_key = os.getenv("BRAVE_SEARCH_API_KEY", "").strip() or os.getenv("BRAVE_API_KEY", "").strip()
    if not api_key or not BRAVE_WRAPPER_OK:
        return {"found": False, "closed": False, "last_date": None}

    try:
        brave = AsyncBrave(api_key=api_key)
        results = await brave.search(q=f"{name} Singapore restaurant", count=5, raw=True)
        web = (results or {}).get("web", {})
        items = web.get("results", []) if isinstance(web, dict) else []
        combined = " ".join((r.get("title", "") + " " + r.get("description", "")) for r in items)

        closed = bool(re.search(r"closed|no.longer|shut.down|defunct", combined, re.IGNORECASE))
        found = bool(items)

        return {"found": found, "closed": closed, "last_date": None}
    except Exception:
        return {"found": False, "closed": False, "last_date": None}


async def _qwant_signal(name: str) -> dict:
    """Qwant public search fallback (non-Google/non-Microsoft)."""
    try:
        async with httpx.AsyncClient(timeout=8, headers={"User-Agent": "osm-sg-validator/1.0"}) as client:
            resp = await client.get(
                "https://api.qwant.com/v3/search/web",
                params={"q": f"{name} Singapore", "count": 5, "locale": "en_US"},
            )
            if resp.status_code >= 400:
                return {"found": False, "closed": False, "last_date": None}
            data = resp.json()

        items = data.get("data", {}).get("result", {}).get("items", [])
        combined = " ".join((i.get("title", "") + " " + i.get("desc", "")) for i in items)
        closed = bool(re.search(r"closed|no.longer|shut.down|defunct", combined, re.IGNORECASE))
        return {"found": bool(items), "closed": closed, "last_date": None}
    except Exception:
        return {"found": False, "closed": False, "last_date": None}