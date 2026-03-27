import asyncio
import aiohttp
from typing import Dict, Any

async def check_wikidata(name: str) -> Dict[str, Any]:
    """
    SPARQL query for P576 dissolved + P582 end time.
    Targeted specifically at checking if a POI is dissolved.
    Returns: dict with source, signal, confidence, detail, and explicit boolean/date flags.
    """
    sparql = f"""
    SELECT ?item ?itemLabel ?dissolvedDate ?endTime WHERE {{
      ?item rdfs:label "{name}"@en .
      OPTIONAL {{ ?item wdt:P576 ?dissolvedDate . }}
      OPTIONAL {{ ?item wdt:P582 ?endTime . }}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
    }} LIMIT 5
    """
    url = "https://query.wikidata.org/sparql"
    headers = {
        "Accept": "application/sparql-results+json",
        "User-Agent": "osm-sg-validator/1.0 (https://github.com/osm-sg)"
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params={"query": sparql}, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    return {
                        "source": "wikidata",
                        "signal": "unknown",
                        "confidence": 0.0,
                        "detail": f"HTTP {resp.status} from Wikidata: {text[:100]}",
                        "wikidata_dissolved": False,
                        "dissolved_date": None
                    }
                    
                data = await resp.json()
                bindings = data.get("results", {}).get("bindings", [])
                
                if bindings:
                    b = bindings[0]
                    dissolved = b.get("dissolvedDate", {}).get("value")
                    end_time = b.get("endTime", {}).get("value")
                    date_val = dissolved or end_time
                    
                    if date_val:
                        return {
                            "source": "wikidata",
                            "signal": "closed",
                            "confidence": 0.95,
                            "detail": f"Wikidata indicates dissolved/ended on: {date_val}",
                            "wikidata_dissolved": True,
                            "dissolved_date": date_val
                        }
                    else:
                        return {
                            "source": "wikidata",
                            "signal": "active",
                            "confidence": 0.5,
                            "detail": "Found in Wikidata but no dissolved/end date.",
                            "wikidata_dissolved": False,
                            "dissolved_date": None
                        }
                        
                return {
                    "source": "wikidata",
                    "signal": "unknown",
                    "confidence": 0.0,
                    "detail": "No matching item found in Wikidata.",
                    "wikidata_dissolved": False,
                    "dissolved_date": None
                }
                
    except asyncio.TimeoutError:
        return {
            "source": "wikidata",
            "signal": "unknown",
            "confidence": 0.0,
            "detail": "Wikidata query timed out.",
            "wikidata_dissolved": False,
            "dissolved_date": None
        }
    except Exception as e:
        return {
            "source": "wikidata",
            "signal": "unknown",
            "confidence": 0.0,
            "detail": f"Error querying Wikidata: {str(e)}",
            "wikidata_dissolved": False,
            "dissolved_date": None
        }

if __name__ == "__main__":
    # Quick standalone test
    async def main():
        print("Testing 'Underwater World Singapore' (Expected: Dissolved)")
        res1 = await check_wikidata("Underwater World Singapore")
        print(res1)
        
        print("\nTesting 'Singapore Zoo' (Expected: Active)")
        res2 = await check_wikidata("Singapore Zoo")
        print(res2)

        print("\nTesting 'Jurong Bird Park' (Expected: Dissolved)")
        res3 = await check_wikidata("Jurong Bird Park")
        print(res3)

    asyncio.run(main())
