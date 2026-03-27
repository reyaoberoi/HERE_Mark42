# app/sources/wikidata.py
import httpx

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"


async def fetch_wikidata(name: str, osm_id: str = None) -> dict:
    """
    Query Wikidata for P576 (dissolved/demolished) and P582 (end time).
    Uses fuzzy label matching in Singapore to reduce false UNKNOWN outcomes.
    """
    try:
        safe_name = name.replace('"', '\\"').replace("'", "\\'")
        query = f"""
        SELECT ?place ?label ?dissolved ?endtime WHERE {{
          ?place rdfs:label ?label .
          FILTER(LANG(?label) = "en")
          FILTER(CONTAINS(LCASE(?label), LCASE("{safe_name}")))
          ?place wdt:P17 wd:Q334 .
          OPTIONAL {{ ?place wdt:P576 ?dissolved }}
          OPTIONAL {{ ?place wdt:P582 ?endtime }}
        }} LIMIT 8
        """

        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                SPARQL_ENDPOINT,
                params={"query": query, "format": "json"},
                headers={
                    "User-Agent": "osm-sg-validator/1.0",
                    "Accept": "application/sparql-results+json",
                },
            )
            data = resp.json()

        bindings = data.get("results", {}).get("bindings", [])
        if not bindings:
            return {
                "source": "wikidata",
                "status": "UNKNOWN",
                "confidence": 0.0,
                "detail": "Not found in Wikidata",
            }

        for b in bindings:
            if "dissolved" in b or "endtime" in b:
                date_val = (b.get("dissolved") or b.get("endtime", {})).get("value", "")
                return {
                    "source": "wikidata",
                    "status": "CLOSED",
                    "confidence": 0.95,
                    "detail": f"Wikidata records dissolution/end date: {date_val[:10]}",
                    "last_activity_date": date_val[:10] if date_val else None,
                }

        return {
            "source": "wikidata",
            "status": "UNKNOWN",
            "confidence": 0.25,
            "detail": f"Found in Wikidata ({len(bindings)} candidates) but no reliable liveness signal",
        }

    except Exception as e:
        return {
            "source": "wikidata",
            "status": "UNKNOWN",
            "confidence": 0.0,
            "detail": str(e),
        }
