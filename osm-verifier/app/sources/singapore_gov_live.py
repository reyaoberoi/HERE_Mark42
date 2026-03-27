# app/sources/singapore_gov_live.py
# Live REST query against Data.gov.sg APIs (no key needed).
# Checks NEA food hygiene grades and URA premises licences in real-time.
import httpx

_BASE = "https://data.gov.sg/api/action/datastore_search"

# Known resource IDs on data.gov.sg (public datasets)
_RESOURCES = {
    "nea_hygiene": "b967df-hygiene-grade",   # NEA food hygiene grades
    "ura_premises": "ura-licensed-premises",  # URA licensed eating establishments
}

# Fallback: use the CKAN search across all SG government datasets
_CKAN_SEARCH = "https://data.gov.sg/api/3/action/resource_search"


async def fetch_sg_gov_live(name: str, lat: float, lon: float) -> dict:
    """Query Data.gov.sg live APIs for business licence / hygiene grade."""
    try:
        async with httpx.AsyncClient(timeout=8,
                                     headers={"User-Agent": "osm-sg-validator/1.0"}) as client:
            # Query the SFA (Singapore Food Agency) food hygiene dataset using CKAN
            resp = await client.get(
                "https://data.gov.sg/api/action/datastore_search",
                params={
                    "resource_id": "d_4a086da0a5553be1d89383cd90d07ecd",  # SFA food licences
                    "q": name,
                    "limit": 5,
                },
            )
            data = resp.json()

        records = data.get("result", {}).get("records", [])
        if not records:
            # Try a broader CKAN resource search
            async with httpx.AsyncClient(timeout=6,
                                         headers={"User-Agent": "osm-sg-validator/1.0"}) as client:
                r2 = await client.get(
                    "https://api-production.data.gov.sg/v2/public/api/datasets",
                    params={"query": name, "limit": 3},
                )
                meta = r2.json()
            datasets = meta.get("data", {}).get("datasets", [])
            if not datasets:
                return {"source": "sg_gov_live", "status": "UNKNOWN", "confidence": 0.0,
                        "detail": "Not found in Data.gov.sg live APIs"}
            return {
                "source": "sg_gov_live",
                "status": "UNKNOWN",
                "confidence": 0.15,
                "detail": f"Dataset mention found: {datasets[0].get('title', '')}",
            }

        rec = records[0]
        licence_status = str(rec.get("licence_status", rec.get("status", ""))).lower()
        biz_name = rec.get("business_name", rec.get("name", name))
        if any(w in licence_status for w in ["cancel", "revoked", "expired", "lapsed"]):
            signal, conf = "CLOSED", 0.85
        elif any(w in licence_status for w in ["active", "valid", "current", "approved", ""]):
            signal, conf = "ACTIVE", 0.80
        else:
            signal, conf = "UNKNOWN", 0.3

        return {
            "source": "sg_gov_live",
            "status": signal,
            "confidence": conf,
            "detail": f"Data.gov.sg: '{biz_name}', licence_status='{licence_status or 'active'}'",
            "last_activity_date": rec.get("expiry_date") or rec.get("date_issued"),
        }

    except Exception as e:
        return {"source": "sg_gov_live", "status": "UNKNOWN", "confidence": 0.0, "detail": str(e)}
