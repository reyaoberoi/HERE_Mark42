"""
S4 — Singapore Government Static Data
Person B owns this file.
Sources: NEA food licences, hawker stalls, STB tourism (all from data.gov.sg)
         + Wikidata SPARQL for dissolved/end-time flags
"""

import sqlite3
import asyncio
import aiohttp
from pathlib import Path
from sentence_transformers import SentenceTransformer, util

DB_PATH = Path("gov_data.sqlite")
MODEL = None  # lazy load

# ── Fuzzy match threshold ──────────────────────────────────────────────────
MATCH_THRESHOLD = 0.82

# ── Dataset download URLs (no login needed) ────────────────────────────────
DATASETS = {
    "nea_food": "https://data.gov.sg/api/action/datastore_search?resource_id=d_4a686577e74131a8d5bc9a7cf6b8a559&limit=10000",
    "hawker":   "https://data.gov.sg/api/action/datastore_search?resource_id=d_bda4baa634dd1cc7a6189bef827d77ab&limit=10000",
    "stb":      "https://data.gov.sg/api/action/datastore_search?resource_id=d_1efe4728b5b8dc70f46a61e286490174&limit=10000",
}


def _get_model():
    global MODEL
    if MODEL is None:
        MODEL = SentenceTransformer("all-MiniLM-L6-v2")
    return MODEL


def init_db():
    """Download datasets and load into SQLite. Run once at startup."""
    import requests

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS pois (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            name TEXT,
            postal_code TEXT,
            licence_status TEXT,
            raw TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_name_postal ON pois(name, postal_code)")
    conn.commit()

    # Only download if table is empty
    cur.execute("SELECT COUNT(*) FROM pois")
    if cur.fetchone()[0] > 0:
        conn.close()
        return

    for source_name, url in DATASETS.items():
        try:
            r = requests.get(url, timeout=15)
            records = r.json().get("result", {}).get("records", [])
            for rec in records:
                name = rec.get("name") or rec.get("business_name") or rec.get("NAME") or ""
                postal = rec.get("postal_code") or rec.get("POSTAL_CODE") or rec.get("addresspostalcode") or ""
                status = rec.get("licence_status") or rec.get("status") or "UNKNOWN"
                cur.execute(
                    "INSERT INTO pois (source, name, postal_code, licence_status, raw) VALUES (?,?,?,?,?)",
                    (source_name, str(name).strip(), str(postal).strip(), str(status).strip(), str(rec))
                )
            conn.commit()
            print(f"[gov_data] Loaded {len(records)} records from {source_name}")
        except Exception as e:
            print(f"[gov_data] Failed to load {source_name}: {e}")

    conn.close()


def _fuzzy_lookup(name: str, postal_code: str = "") -> dict:
    """Return best fuzzy match from SQLite using sentence-transformers."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Pull candidates — filter by postal if provided
    if postal_code:
        cur.execute("SELECT name, licence_status, source FROM pois WHERE postal_code=?", (postal_code,))
        rows = cur.fetchall()
    else:
        rows = []

    # Fallback: all rows (capped for speed)
    if not rows:
        cur.execute("SELECT name, licence_status, source FROM pois LIMIT 5000")
        rows = cur.fetchall()
    conn.close()

    if not rows:
        return {"gov_listed": False, "licence_status": "UNKNOWN", "match_score": 0.0, "matched_source": None}

    model = _get_model()
    names = [r[0] for r in rows]
    query_emb = model.encode(name, convert_to_tensor=True)
    corpus_emb = model.encode(names, convert_to_tensor=True)
    scores = util.cos_sim(query_emb, corpus_emb)[0]
    best_idx = int(scores.argmax())
    best_score = float(scores[best_idx])

    if best_score >= MATCH_THRESHOLD:
        return {
            "gov_listed": True,
            "licence_status": rows[best_idx][1],
            "match_score": round(best_score, 3),
            "matched_source": rows[best_idx][2],
            "matched_name": names[best_idx],
        }
    return {"gov_listed": False, "licence_status": "UNKNOWN", "match_score": round(best_score, 3), "matched_source": None}


async def check_wikidata(name: str) -> dict:
    """SPARQL query for P576 dissolved + P582 end time."""
    sparql = f"""
    SELECT ?item ?dissolvedDate ?endTime WHERE {{
      ?item rdfs:label "{name}"@en .
      OPTIONAL {{ ?item wdt:P576 ?dissolvedDate . }}
      OPTIONAL {{ ?item wdt:P582 ?endTime . }}
    }} LIMIT 5
    """
    url = "https://query.wikidata.org/sparql"
    headers = {"Accept": "application/json", "User-Agent": "osm-sg-validator/1.0"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params={"query": sparql}, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                data = await resp.json()
                bindings = data.get("results", {}).get("bindings", [])
                if bindings:
                    b = bindings[0]
                    dissolved = b.get("dissolvedDate", {}).get("value")
                    end_time = b.get("endTime", {}).get("value")
                    if dissolved or end_time:
                        return {"wikidata_dissolved": True, "dissolved_date": dissolved or end_time}
                return {"wikidata_dissolved": False, "dissolved_date": None}
    except Exception as e:
        print(f"[wikidata] Error: {e}")
        return {"wikidata_dissolved": False, "dissolved_date": None}


async def check_gov_data(name: str, postal_code: str = "") -> dict:
    """
    Main entry point. Returns combined gov + wikidata signal.
    Returns dict with keys: signal, gov_listed, licence_status,
                             wikidata_dissolved, detail
    """
    try:
        # Ensure DB is ready
        if not DB_PATH.exists():
            init_db()

        gov_result = _fuzzy_lookup(name, postal_code)
        wiki_result = await check_wikidata(name)

        # Determine signal
        if wiki_result["wikidata_dissolved"]:
            signal = "closed"
            detail = f"Wikidata dissolved: {wiki_result['dissolved_date']}"
        elif gov_result["gov_listed"]:
            status = gov_result["licence_status"].lower()
            if any(w in status for w in ["active", "valid", "approved"]):
                signal = "active"
            elif any(w in status for w in ["lapsed", "cancelled", "expired", "revoked", "closed"]):
                signal = "closed"
            else:
                signal = "unknown"
            detail = f"Gov match ({gov_result['matched_source']}): {gov_result.get('matched_name')} — {gov_result['licence_status']} (score {gov_result['match_score']})"
        else:
            signal = "unknown"
            detail = f"No gov match (best score {gov_result['match_score']})"

        return {
            "source": "gov_data",
            "signal": signal,
            "confidence": 0.9 if gov_result["gov_listed"] or wiki_result["wikidata_dissolved"] else 0.3,
            "detail": detail,
            "gov_listed": gov_result["gov_listed"],
            "licence_status": gov_result["licence_status"],
            "wikidata_dissolved": wiki_result["wikidata_dissolved"],
        }
    except Exception as e:
        print(f"[gov_data] Unexpected error: {e}")
        return {"source": "gov_data", "signal": "unknown", "confidence": 0.0, "detail": str(e)}


# ── Quick test ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    result = asyncio.run(check_gov_data("Lau Pa Sat", "048023"))
    print(result)