# app/sources/gov_data.py
# Uses TF-IDF cosine similarity (scikit-learn) to match SG government datasets.
# Avoids sentence-transformers per-row embedding — 10-100x faster, no extra download.
import sqlite3
import re
from functools import lru_cache
from pathlib import Path

DB_PATH = str(Path(__file__).resolve().parents[2] / "gov_data.sqlite")

_vectorizer = None
_corpus = None     # list of (name, status, address, table)
_matrix = None     # TF-IDF matrix


def _normalise(text: str) -> str:
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def _load_corpus():
    global _vectorizer, _corpus, _matrix
    if _matrix is not None:
        return
    from sklearn.feature_extraction.text import TfidfVectorizer
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = []
        for table in ["nea_food", "hawker_stalls", "stb_tourism"]:
            try:
                for name, status, addr in conn.execute(
                    f"SELECT name, status, address FROM {table} LIMIT 5000"
                ):
                    if name:
                        rows.append((_normalise(name), status or "", addr or "", table))
            except Exception:
                continue
        conn.close()
    except Exception:
        rows = []

    if not rows:
        _corpus, _matrix = [], None
        return

    _corpus = rows
    _vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4))
    _matrix = _vectorizer.fit_transform([r[0] for r in rows])


async def fetch_gov_data(name: str, lat: float, lon: float, postal_code: str = "") -> dict:
    try:
        import numpy as np
        _load_corpus()
        if _matrix is None:
            return {"source": "gov_data", "status": "UNKNOWN", "confidence": 0.0,
                    "detail": "gov_data.sqlite not populated"}

        query_vec = _vectorizer.transform([_normalise(name)])
        # cosine similarity via dot product on normalised TF-IDF
        from sklearn.metrics.pairwise import cosine_similarity
        scores = cosine_similarity(query_vec, _matrix)[0]
        best_idx = int(np.argmax(scores))
        best_score = float(scores[best_idx])

        THRESHOLD = 0.55  # strong match
        WEAK_THRESHOLD = 0.35
        if best_score < WEAK_THRESHOLD:
            return {"source": "gov_data", "status": "UNKNOWN", "confidence": 0.0,
                    "detail": f"No SG govt match (best {best_score:.2f})"}

        row_name, status, addr, table = _corpus[best_idx]
        raw = status.lower()
        if any(w in raw for w in ["cancel", "revok", "expir", "closed", "void", "ceased"]):
            signal = "CLOSED"
        elif raw in ("active", "valid", "approved", ""):
            signal = "ACTIVE"
        else:
            signal = "UNKNOWN"

        if best_score < THRESHOLD and signal == "UNKNOWN":
            signal = "ACTIVE"
            conf = round(min(max(best_score * 0.9, 0.30), 0.55), 3)
            return {
                "source": "gov_data",
                "status": signal,
                "confidence": conf,
                "detail": f"{table}: weak match '{row_name}' (score {best_score:.2f})",
                "last_activity_date": None,
            }

        return {
            "source": "gov_data",
            "status": signal,
            "confidence": round(min(best_score * 1.1, 1.0), 3),
            "detail": f"{table}: matched '{row_name}' (score {best_score:.2f}), status={status or 'active'}",
            "last_activity_date": None,
        }
    except Exception as e:
        return {"source": "gov_data", "status": "UNKNOWN", "confidence": 0.0, "detail": str(e)}