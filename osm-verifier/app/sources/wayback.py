# app/sources/wayback.py
import httpx
from datetime import datetime

CDX_API = "https://web.archive.org/cdx/search/cdx"


async def fetch_wayback(website_url: str = None) -> dict:
    """
    Query Wayback Machine CDX API for domain crawl history.
    A website that stopped being crawled = domain went dark = strong closure signal.
    """
    if not website_url:
        return {"source": "wayback", "status": "UNKNOWN", "confidence": 0.0,
                "detail": "No website URL in OSM tags"}

    try:
        domain = website_url.replace("https://", "").replace("http://", "").split("/")[0]

        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(CDX_API, params={
                "url": domain,
                "output": "json",
                "limit": 5,
                "fl": "timestamp,statuscode",
                "filter": "statuscode:200",
                "fastLatest": "true",
                "from": "20150101",
            })
            rows = resp.json()

        if not rows or len(rows) <= 1:
            try:
                async with httpx.AsyncClient(timeout=5) as cl:
                    probe = await cl.get(f"https://{domain}", follow_redirects=True)
                    if probe.status_code < 400:
                        return {
                            "source": "wayback",
                            "status": "ACTIVE",
                            "confidence": 0.55,
                            "detail": f"Domain {domain} is live (no useful CDX history)",
                        }
            except Exception:
                pass
            return {
                "source": "wayback",
                "status": "UNKNOWN",
                "confidence": 0.25,
                "detail": f"Domain {domain} has limited crawl data",
            }

        data_rows = rows[1:]
        if not data_rows:
            return {"source": "wayback", "status": "UNKNOWN", "confidence": 0.0,
                    "detail": f"No crawl data for {domain}"}

        last_ts = data_rows[-1][0]
        last_dt = datetime.strptime(last_ts, "%Y%m%d%H%M%S")
        days_since = (datetime.utcnow() - last_dt).days

        # Check if site is still live
        site_live = False
        try:
            async with httpx.AsyncClient(timeout=5) as cl:
                probe = await cl.get(f"https://{domain}", follow_redirects=True)
                site_live = probe.status_code < 400
        except Exception:
            pass

        if site_live:
            return {
                "source": "wayback",
                "status": "ACTIVE",
                "confidence": 0.70,
                "detail": f"Website {domain} is currently live",
                "last_activity_date": last_dt.strftime("%Y-%m-%d"),
            }
        elif days_since > 365:
            return {
                "source": "wayback",
                "status": "CLOSED",
                "confidence": 0.65,
                "detail": f"Website {domain} last crawled {days_since} days ago — domain appears dead",
                "last_activity_date": last_dt.strftime("%Y-%m-%d"),
            }
        else:
            return {
                "source": "wayback",
                "status": "UNKNOWN",
                "confidence": 0.30,
                "detail": f"Last crawl {days_since} days ago — site may have moved",
                "last_activity_date": last_dt.strftime("%Y-%m-%d"),
            }

    except Exception as e:
        return {"source": "wayback", "status": "UNKNOWN", "confidence": 0.0, "detail": str(e)}
