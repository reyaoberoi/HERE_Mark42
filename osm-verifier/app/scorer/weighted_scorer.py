# app/scorer/weighted_scorer.py
from models import SourceSignal
from typing import List

# Likelihood ratios per source when signal = ACTIVE or CLOSED
LR_TABLE = {
    "gov_data":       {"ACTIVE": 5.0, "CLOSED": 0.10},
    "food_platforms": {"ACTIVE": 4.0, "CLOSED": 0.12},
    "mapillary":      {"ACTIVE": 3.5, "CLOSED": 0.15},
    "reddit":         {"ACTIVE": 2.5, "CLOSED": 0.40},
    "wayback":        {"ACTIVE": 2.0, "CLOSED": 0.20},
    "wikidata":       {"ACTIVE": 1.5, "CLOSED": 0.05},
}

HIGH_WEIGHT_SOURCES = {"gov_data", "food_platforms", "mapillary"}


def compute_score(geo: dict, stats_ctx: dict, gov: dict, food: dict,
                  social: dict, mapillary: dict, wikidata: dict, wayback: dict) -> dict:

    prior = stats_ctx.get("prior_p_active", 0.70)

    source_map = {
        "gov_data":      gov,
        "food_platforms": food,
        "reddit":        social,
        "mapillary":     mapillary,
        "wikidata":      wikidata,
        "wayback":       wayback,
    }

    posterior = prior
    sources_out: List[SourceSignal] = []
    confirmed_from = []
    active_high = []
    closed_high = []

    for source_name, result in source_map.items():
        status = result.get("status", "UNKNOWN")
        conf   = result.get("confidence", 0.0)
        detail = result.get("detail", "")
        lrd    = result.get("last_activity_date")

        sources_out.append(SourceSignal(
            source=source_name,
            status=status,
            confidence=conf,
            last_activity_date=lrd,
            detail=detail,
        ))

        if status == "UNKNOWN":
            continue  # LR = 1.0, no update

        lr = LR_TABLE.get(source_name, {}).get(status, 1.0)
        posterior = _lr_update(posterior, lr)

        if status == "ACTIVE":
            confirmed_from.append(source_name)
            if source_name in HIGH_WEIGHT_SOURCES:
                active_high.append(source_name)
        elif status == "CLOSED":
            if source_name in HIGH_WEIGHT_SOURCES:
                closed_high.append(source_name)

    # Staleness penalty: if edit_age > 730 days, reduce posterior by 15%
    edit_age = geo.get("edit_age_days", 0) or 0
    if edit_age > 730:
        posterior *= 0.85

    # Conflict detection: high-weight sources disagree
    conflict_flag = bool(active_high and closed_high)

    # Convert posterior to 0-100 confidence
    confidence = int(round(posterior * 100))
    confidence = max(0, min(100, confidence))

    # Recommendation
    if conflict_flag:
        recommendation = "REVIEW"
    elif posterior >= 0.78:
        recommendation = "ACCEPT"
    elif posterior >= 0.42:
        recommendation = "REVIEW"
    else:
        recommendation = "REJECT"

    # Predicted status
    osm_found = geo.get("osm_found", False)
    if not osm_found and confidence > 50:
        predicted_status = "New Place"
    elif confidence < 40:
        predicted_status = "Recently Closed"
    elif confidence > 75:
        predicted_status = "Established"
    else:
        predicted_status = "Uncertain"

    narrative = build_narrative(sources_out, recommendation, confidence, conflict_flag)

    return {
        "confidence": confidence,
        "recommendation": recommendation,
        "predicted_status": predicted_status,
        "sources": sources_out,
        "narrative": narrative,
        "conflict_flag": conflict_flag,
        "confirmed_from": confirmed_from,
        "posterior": posterior,
    }


def _lr_update(prior: float, lr: float) -> float:
    """Bayesian LR update: posterior odds = prior odds * LR."""
    if prior <= 0:
        return 0.0
    if prior >= 1:
        return 1.0
    prior_odds = prior / (1 - prior)
    posterior_odds = prior_odds * lr
    return posterior_odds / (1 + posterior_odds)


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
