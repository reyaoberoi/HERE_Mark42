"""
S5 — Burpple + HungryGoWhere Scraper
Person B owns this file.
Extracts: last review date, closed badge per platform.
Uses Playwright headless — search snippet only, not full listing.
"""

import asyncio
import re
from datetime import datetime
import dateparser
from playwright.async_api import async_playwright


TIMEOUT_MS = 8000


async def _scrape_burpple(name: str, location: str = "Singapore") -> dict:
    """Scrape Burpple search snippet for last review date + closed badge."""
    result = {
        "source": "burpple",
        "scrape_found": False,
        "last_activity_date": None,
        "closed_signal": False,
        "detail": None,
    }
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_extra_http_headers({"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"})

            query = f"{name} {location}"
            url = f"https://www.burpple.com/search/food?q={query.replace(' ', '+')}"
            await page.goto(url, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)

            content = await page.content()
            await browser.close()

        # Check for closed badge
        closed_patterns = [r"permanently\s+closed", r"closed\s+down", r"no\s+longer\s+operating"]
        for pat in closed_patterns:
            if re.search(pat, content, re.IGNORECASE):
                result["closed_signal"] = True
                result["scrape_found"] = True
                result["detail"] = "Burpple closed badge detected"
                return result

        # Extract date mentions from snippet text
        date_pattern = r'(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}|\d{4}-\d{2}-\d{2}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})'
        dates_found = re.findall(date_pattern, content, re.IGNORECASE)

        if name.lower() in content.lower():
            result["scrape_found"] = True

        if dates_found:
            parsed_dates = []
            for d in dates_found[:10]:
                parsed = dateparser.parse(d)
                if parsed:
                    parsed_dates.append(parsed)
            if parsed_dates:
                latest = max(parsed_dates)
                result["last_activity_date"] = latest.strftime("%Y-%m-%d")
                result["scrape_found"] = True
                result["detail"] = f"Last activity: {result['last_activity_date']}"

        if result["scrape_found"] and not result["detail"]:
            result["detail"] = "Found on Burpple, no date extracted"

    except Exception as e:
        result["detail"] = f"Burpple scrape error: {str(e)[:100]}"

    return result


async def _scrape_hungrygowhere(name: str, location: str = "Singapore") -> dict:
    """Scrape HungryGoWhere search snippet."""
    result = {
        "source": "hungrygowhere",
        "scrape_found": False,
        "last_activity_date": None,
        "closed_signal": False,
        "detail": None,
    }
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_extra_http_headers({"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"})

            query = f"{name} {location}"
            url = f"https://www.hungrygowhere.com/search/?query={query.replace(' ', '+')}"
            await page.goto(url, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)

            content = await page.content()
            await browser.close()

        # Closed signal
        if re.search(r"permanently\s+closed|no\s+longer|closed\s+down", content, re.IGNORECASE):
            result["closed_signal"] = True
            result["scrape_found"] = True
            result["detail"] = "HungryGoWhere closed signal detected"
            return result

        if name.lower() in content.lower():
            result["scrape_found"] = True

        # Date extraction
        date_pattern = r'(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}|\d{4}-\d{2}-\d{2})'
        dates_found = re.findall(date_pattern, content, re.IGNORECASE)

        if dates_found:
            parsed_dates = [dateparser.parse(d) for d in dates_found[:10] if dateparser.parse(d)]
            if parsed_dates:
                latest = max(parsed_dates)
                result["last_activity_date"] = latest.strftime("%Y-%m-%d")
                result["detail"] = f"Last activity: {result['last_activity_date']}"

        if result["scrape_found"] and not result["detail"]:
            result["detail"] = "Found on HungryGoWhere, no date extracted"

    except Exception as e:
        result["detail"] = f"HungryGoWhere scrape error: {str(e)[:100]}"

    return result


async def check_food_platforms(name: str, location: str = "Singapore") -> dict:
    """
    Run Burpple + HungryGoWhere in parallel.
    Returns combined signal dict.
    """
    try:
        burpple, hgw = await asyncio.gather(
            _scrape_burpple(name, location),
            _scrape_hungrygowhere(name, location),
            return_exceptions=True
        )

        # Handle exceptions from gather
        if isinstance(burpple, Exception):
            burpple = {"source": "burpple", "scrape_found": False, "last_activity_date": None, "closed_signal": False, "detail": str(burpple)}
        if isinstance(hgw, Exception):
            hgw = {"source": "hungrygowhere", "scrape_found": False, "last_activity_date": None, "closed_signal": False, "detail": str(hgw)}

        # Determine combined signal
        closed = burpple["closed_signal"] or hgw["closed_signal"]

        # Latest date across both
        dates = [d for d in [burpple["last_activity_date"], hgw["last_activity_date"]] if d]
        last_date = max(dates) if dates else None

        if closed:
            signal = "closed"
            confidence = 0.85
        elif last_date:
            # Older than 18 months → flatline signal
            try:
                age_days = (datetime.now() - datetime.strptime(last_date, "%Y-%m-%d")).days
                if age_days > 548:  # 18 months
                    signal = "closed"
                    confidence = 0.6
                else:
                    signal = "active"
                    confidence = 0.7
            except Exception:
                signal = "unknown"
                confidence = 0.3
        elif burpple["scrape_found"] or hgw["scrape_found"]:
            signal = "unknown"
            confidence = 0.3
        else:
            signal = "unknown"
            confidence = 0.1

        return {
            "source": "food_platforms",
            "signal": signal,
            "confidence": confidence,
            "last_activity_date": last_date,
            "closed_signal": closed,
            "detail": f"Burpple: {burpple['detail']} | HGW: {hgw['detail']}",
            "burpple": burpple,
            "hungrygowhere": hgw,
        }

    except Exception as e:
        return {
            "source": "food_platforms",
            "signal": "unknown",
            "confidence": 0.0,
            "detail": f"Error: {str(e)}",
        }


# ── Quick test ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    result = asyncio.run(check_food_platforms("Lau Pa Sat"))
    print(result)