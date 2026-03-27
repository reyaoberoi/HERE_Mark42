# app/scorer/weighted_scorer.py
import math
from typing import Dict, List

from models import SourceSignal

SOURCE_WEIGHT = {
    "osm_geo": 0.80,
    "gov_data": 1.35,
    "sg_gov_live": 1.40,
    "food_platforms": 1.15,
    "tripadvisor": 0.90,
    "mapillary": 1.55,
    "reddit": 0.65,
    "wayback": 0.85,
    "wikidata": 0.95,
}

STATUS_SIGN = {"ACTIVE": 1.0, "CLOSED": -1.0, "UNKNOWN": 0.0}
HIGH_WEIGHT_SOURCES = {"gov_data", "sg_gov_live", "food_platforms", "mapillary"}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _logit(p: float) -> float:
    p = _clamp(p, 0.001, 0.999)
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _norm_status(raw: str) -> str:
    up = (raw or "UNKNOWN").upper()
    return up if up in STATUS_SIGN else "UNKNOWN"


def compute_score(
    geo: dict,
    stats_ctx: dict,
    gov: dict,
    food: dict,
    social: dict,
    mapillary: dict,
    wikidata: dict,
    wayback: dict,
    sg_gov_live: dict | None = None,
    tripadvisor: dict | None = None,
) -> dict:
    prior = float(stats_ctx.get("prior_p_active", 0.70) or 0.70)
    source_map: Dict[str, dict] = {
        "osm_geo": {
            "status": "ACTIVE" if geo.get("osm_found") else "UNKNOWN",
            "confidence": 0.55 if geo.get("osm_found") else 0.0,
            "detail": "OSM geocoding matched candidate" if geo.get("osm_found") else "No OSM geo match",
            "last_activity_date": None,
        },
        "gov_data": gov or {},
        "sg_gov_live": sg_gov_live or {},
        "food_platforms": food or {},
        "tripadvisor": tripadvisor or {},
        "reddit": social or {},
        "mapillary": mapillary or {},
        "wikidata": wikidata or {},
        "wayback": wayback or {},
    }

    sources_out: List[SourceSignal] = []
    considered_sources: List[str] = []
    active_sources: List[str] = []
    closure_sources: List[str] = []
    active_high: List[str] = []
    closed_high: List[str] = []

    evidence_sum = 0.0
    for source_name, result in source_map.items():
        status = _norm_status(result.get("status", "UNKNOWN"))
        conf = _clamp(float(result.get("confidence", 0.0) or 0.0), 0.0, 1.0)
        detail = (result.get("detail") or "").strip()
        last_activity_date = result.get("last_activity_date")

        sources_out.append(
            SourceSignal(
                source=source_name,
                status=status,
                confidence=conf,
                last_activity_date=last_activity_date,
                detail=detail,
            )
        )

        if status != "UNKNOWN" or detail:
            considered_sources.append(source_name)

        sign = STATUS_SIGN.get(status, 0.0)
        if sign == 0.0:
            continue

        calibrated_conf = 0.35 + 0.65 * conf
        weighted_contrib = sign * SOURCE_WEIGHT.get(source_name, 0.7) * calibrated_conf
        evidence_sum += weighted_contrib

        if status == "ACTIVE":
            active_sources.append(source_name)
            if source_name in HIGH_WEIGHT_SOURCES:
                active_high.append(source_name)
        elif status == "CLOSED":
            closure_sources.append(source_name)
            if source_name in HIGH_WEIGHT_SOURCES:
                closed_high.append(source_name)

    # Geographic and freshness priors improve stability for sparse signals.
    geo_bias = 0.18 if geo.get("osm_found") else -0.12
    edit_age = int(geo.get("edit_age_days", 0) or 0)
    age_penalty = _clamp(edit_age / 3650.0, 0.0, 0.55)

    posterior = _sigmoid(_logit(prior) + 1.08 * evidence_sum + geo_bias - age_penalty)

    mapillary_status = _norm_status((source_map.get("mapillary") or {}).get("status", "UNKNOWN"))
    mapillary_conf = _clamp(float((source_map.get("mapillary") or {}).get("confidence", 0.0) or 0.0), 0.0, 1.0)
    strong_mapillary_closed = mapillary_status == "CLOSED" and mapillary_conf >= 0.60

    non_osm_active = [s for s in active_sources if s != "osm_geo"]
    non_osm_closed = [s for s in closure_sources if s != "osm_geo"]

    conflict_flag = bool(active_high and closed_high)
    if conflict_flag:
        posterior = 0.5 + (posterior - 0.5) * 0.45

    # OSM-only ACTIVE should not be enough to classify a place as confidently open.
    if geo.get("osm_found") and not non_osm_active and not non_osm_closed:
        posterior = min(posterior, 0.64)

    # If web/visual sources indicate closure and no non-OSM active source supports reopening,
    # bias away from OPEN even if OSM tags are stale.
    if non_osm_closed and not non_osm_active:
        posterior = min(posterior, 0.40)

    # Strong visual closure from Mapillary should not collapse to "uncertain"
    # when only stale OSM tags still claim the place is active.
    if strong_mapillary_closed and geo.get("osm_found"):
        if not non_osm_active:
            posterior = min(posterior, 0.24)

    p_active = _clamp(posterior, 0.0, 1.0)
    p_closed = 1.0 - p_active
    confidence = int(round(max(p_active, p_closed) * 100))

    if conflict_flag:
        recommendation = "REVIEW"
    elif strong_mapillary_closed and p_closed >= 0.72:
        recommendation = "REJECT"
    elif p_active >= 0.72:
        recommendation = "ACCEPT"
    elif p_closed >= 0.62 and (closure_sources or strong_mapillary_closed):
        recommendation = "REJECT"
    else:
        recommendation = "REVIEW"

    # Predicted status is derived from model decision, not place-specific rules.
    if recommendation == "REJECT":
        predicted_status = "Closed"
    elif recommendation == "ACCEPT":
        predicted_status = "Open"
    else:
        predicted_status = "Review"

    contradiction_flag = bool(
        geo.get("osm_found")
        and predicted_status == "Closed"
        and recommendation == "REJECT"
        and not conflict_flag
    )

    narrative = build_narrative(sources_out, recommendation, confidence, conflict_flag)

    return {
        "confidence": confidence,
        "recommendation": recommendation,
        "predicted_status": predicted_status,
        "sources": sources_out,
        "narrative": narrative,
        "conflict_flag": conflict_flag,
        "posterior": p_active,
        "p_closed": p_closed,
        "strong_mapillary_closed": strong_mapillary_closed,
        "contradiction_flag": contradiction_flag,
        "considered_sources": considered_sources,
        "active_sources": active_sources,
        "closure_sources": closure_sources,
    }


def build_narrative(sources: List[SourceSignal], recommendation: str,
                    confidence: int, conflict_flag: bool) -> str:
    """
    Deterministic 2-sentence narrative from the top contributing sources.
    No LLM — pure rule-based template.
    """
    closed_sources = [s for s in sources if s.status == "CLOSED" and s.detail]
    active_sources = [s for s in sources if s.status == "ACTIVE" and s.detail]

    if recommendation == "REJECT":
        top = closed_sources[:2]
        if top:
            s1 = top[0].detail
            s2 = top[1].detail if len(top) > 1 else "No corroborating active signals found."
            return f"{s1}. {s2}"
        return f"Multiple signals indicate this place may be permanently closed (confidence: {confidence}%)."

    elif recommendation == "ACCEPT":
        top = active_sources[:2]
        if top:
            s1 = top[0].detail
            s2 = top[1].detail if len(top) > 1 else "OSM data appears current."
            return f"{s1}. {s2}"
        return f"Available signals indicate this place is likely still operating (confidence: {confidence}%)."

    else:  # REVIEW
        if conflict_flag:
            a = active_sources[0].detail if active_sources else "Some sources suggest active."
            c = closed_sources[0].detail if closed_sources else "Some sources suggest closed."
            return f"Conflicting signals: {a}. However: {c}"
        return f"Inconclusive evidence (confidence: {confidence}%). Manual verification recommended."


def generate_changeset_diff(geo: dict) -> dict:
    """Generate the disused: OSM tag transformation."""
    original_tags = geo.get("tags", {})
    skip_keys = {"source", "note", "disused", "disused:shop", "disused:amenity",
                 "disused:tourism", "disused:leisure"}

    tags_after = {}
    for k, v in original_tags.items():
        if k in skip_keys:
            tags_after[k] = v
        elif k in ("shop", "amenity", "tourism", "leisure", "name", "addr:street",
                   "addr:city", "addr:postcode", "opening_hours", "website",
                   "phone", "contact:website", "contact:phone"):
            tags_after[f"disused:{k}"] = v
        else:
            tags_after[k] = v

    tags_after["disused"] = "yes"
    tags_after["note"] = "Automatically flagged as likely closed by osm-sg-validator"

    return {
        "before": original_tags,
        "after": tags_after,
        "osm_id": geo.get("osm_id"),
        "osm_type": geo.get("osm_type", "node"),
    }
