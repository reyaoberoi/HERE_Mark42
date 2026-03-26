"""
Weighted Scorer — fuses signals from gov_data, food_platforms, social_signals.

Signal vocabulary shared by all sources
    "active"   → POI is operating
    "closed"   → POI is no longer operating
    "unknown"  → no usable signal

Source weights (sum = 1.0)
    gov_data        0.45  — official NEA/STB licences + Wikidata
    food_platforms  0.35  — Burpple + HungryGoWhere live scrape
    social_signal   0.20  — Reddit SG + DuckDuckGo presence

Final score is a weighted sum in [-1, +1]:
    +1 → strongly active
    -1 → strongly closed
     0 → no signal

ACCEPT  >=  0.35   (lean active with reasonable confidence)
REJECT  <= -0.35   (lean closed with reasonable confidence)
REVIEW  otherwise  (conflicting / low-confidence signals)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

# ── Source weights (must sum to 1.0) ───────────────────────────────────────
# gov_data  : official NEA/STB licences + Wikidata  — most authoritative
# geo       : OSM Overpass + edit-age + contributor trust
# food_platforms : Burpple / HungryGoWhere live scrape
# stats     : population-level staleness percentile signal
# social_signal  : Reddit SG + DuckDuckGo presence
WEIGHTS: dict[str, float] = {
    "gov_data":       0.30,
    "geo":            0.25,
    "food_platforms": 0.25,
    "stats":          0.10,
    "social_signal":  0.10,
}

# Signal → numeric vote
SIGNAL_VOTE: dict[str, float] = {
    "active":  +1.0,
    "closed":  -1.0,
    "unknown":  0.0,
}

ACCEPT_THRESHOLD =  0.25
REJECT_THRESHOLD = -0.25


@dataclass
class SourceInput:
    """Normalised input from one source module."""
    source: str                # must match a key in WEIGHTS
    signal: str                # "active" | "closed" | "unknown"
    confidence: float          # 0.0 – 1.0 (how sure the source is)
    detail: Optional[str] = None


@dataclass
class ScorerResult:
    """Output of the weighted scorer."""
    weighted_score: float          # [-1.0, +1.0]
    confidence: float              # 0.0 – 1.0  (normalised certainty)
    recommendation: str            # "ACCEPT" | "REVIEW" | "REJECT"
    narrative: str
    source_breakdown: list[dict] = field(default_factory=list)
    unknown_sources: list[str] = field(default_factory=list)


# ── Internal helpers ───────────────────────────────────────────────────────

def _build_narrative(
    weighted_score: float,
    recommendation: str,
    breakdown: list[dict],
    unknown_sources: list[str],
) -> str:
    """Human-readable explanation of the decision."""
    parts: list[str] = []

    for b in breakdown:
        vote_str = "➕ active" if b["vote"] > 0 else ("➖ closed" if b["vote"] < 0 else "⬜ unknown")
        parts.append(
            f"[{b['source']}] {vote_str} "
            f"(conf {b['source_confidence']:.0%}, "
            f"effective weight {b['effective_weight']:.3f})"
        )

    if unknown_sources:
        parts.append(f"No signal from: {', '.join(unknown_sources)}")

    verdict_str = {
        "ACCEPT": "POI appears to be active — accepting map data",
        "REJECT": "POI appears to be permanently closed — flagging for removal",
        "REVIEW": "Conflicting or insufficient signals — queuing for human review",
    }[recommendation]

    return (
        f"{verdict_str}. "
        f"Weighted score: {weighted_score:+.3f}. "
        + " | ".join(parts)
    )


# ── Main entry point ────────────────────────────────────────────────────────

def compute_score(sources: list[SourceInput]) -> ScorerResult:
    """
    Fuse signals from one or more SourceInput objects into a single verdict.

    Key behaviours:
    - Weight redistribution: sources with unknown signal AND confidence <= 0.2
      (errors, no data) are excluded; their weight is redistributed to sources
      that actually produced a signal.
    - Signal agreement bonus: if >=2 sources agree on active/closed, confidence
      gets a 10% boost.
    - Wikidata-dissolved veto: if gov_data is 'closed' with high confidence
      it acts as a hard floor pushing the score toward REJECT.
    """
    source_map: dict[str, SourceInput] = {s.source: s for s in sources}

    # Check for Wikidata veto (gov_data signals closed at high confidence)
    gov = source_map.get("gov_data")
    wikidata_veto = (
        gov is not None
        and gov.signal == "closed"
        and gov.confidence >= 0.85
    )

    # ── Classify sources into voting vs excluded ───────────────────────────
    # Excluded: unknown signal with very low confidence (errors, no data)
    LOW_CONF_THRESHOLD = 0.45
    voting_sources: list[SourceInput] = []
    excluded_sources: list[str] = []
    unknown_sources: list[str] = []

    for source_name in WEIGHTS:
        src = source_map.get(source_name)
        if src is None:
            excluded_sources.append(source_name)
            unknown_sources.append(source_name)
            continue

        if src.signal == "unknown" and src.confidence <= LOW_CONF_THRESHOLD:
            # This source errored out or has no useful data — exclude it
            excluded_sources.append(source_name)
            unknown_sources.append(source_name)
        else:
            voting_sources.append(src)
            if src.signal == "unknown":
                unknown_sources.append(source_name)

    # ── Redistribute weight from excluded sources ─────────────────────────
    total_excluded_weight = sum(WEIGHTS.get(s, 0) for s in excluded_sources)
    total_voting_base_weight = sum(WEIGHTS.get(s.source, 0) for s in voting_sources)

    # Redistribution factor: scale up voting sources' weights proportionally
    if total_voting_base_weight > 0:
        redistribution_factor = 1.0 + (total_excluded_weight / total_voting_base_weight)
    else:
        redistribution_factor = 1.0

    # ── Compute weighted score ────────────────────────────────────────────
    weighted_sum = 0.0
    total_effective_weight = 0.0
    breakdown: list[dict] = []

    for src in voting_sources:
        vote = SIGNAL_VOTE.get(src.signal, 0.0)
        effective_confidence = max(0.0, min(1.0, src.confidence))
        base_weight = WEIGHTS.get(src.source, 0.0)
        # Redistributed weight × confidence
        effective_weight = base_weight * redistribution_factor * effective_confidence

        weighted_sum += vote * effective_weight
        total_effective_weight += effective_weight

        breakdown.append({
            "source": src.source,
            "signal": src.signal,
            "vote": vote,
            "source_confidence": effective_confidence,
            "base_weight": base_weight,
            "effective_weight": round(effective_weight, 4),
            "detail": src.detail,
        })

    # Also add excluded sources to breakdown for transparency
    for source_name in excluded_sources:
        src = source_map.get(source_name)
        if src is not None:
            breakdown.append({
                "source": source_name,
                "signal": src.signal,
                "vote": 0.0,
                "source_confidence": src.confidence,
                "base_weight": WEIGHTS.get(source_name, 0.0),
                "effective_weight": 0.0,
                "detail": f"[excluded — low confidence] {src.detail}",
            })

    # Normalise score to [-1, +1]
    if total_effective_weight > 0:
        weighted_score = weighted_sum / total_effective_weight
    else:
        weighted_score = 0.0

    # ── Signal agreement bonus ────────────────────────────────────────────
    active_count = sum(1 for s in voting_sources if s.signal == "active")
    closed_count = sum(1 for s in voting_sources if s.signal == "closed")
    agreement_bonus = 0.0
    if active_count >= 2 or closed_count >= 2:
        agreement_bonus = 0.10

    # ── Confidence calculation ────────────────────────────────────────────
    # Weighted average of voting sources' confidences, scaled by coverage
    if voting_sources:
        voting_conf_sum = sum(
            WEIGHTS.get(s.source, 0) * s.confidence
            for s in voting_sources
            if s.signal != "unknown"
        )
        max_possible_weight = sum(WEIGHTS.values())
        coverage_ratio = total_voting_base_weight / max_possible_weight if max_possible_weight > 0 else 0

        # Weighted average of confidence from sources that actually voted
        if total_voting_base_weight > 0:
            avg_conf = voting_conf_sum / total_voting_base_weight
        else:
            avg_conf = 0.0

        # Tiered coverage penalty
        if coverage_ratio >= 0.6:
            coverage_penalty = 1.0
        elif coverage_ratio >= 0.3:
            coverage_penalty = 0.8
        else:
            coverage_penalty = 0.6

        raw_confidence = (avg_conf * coverage_penalty) + agreement_bonus
        confidence = round(min(1.0, raw_confidence), 3)

        # Floor: if any source voted with real data, confidence >= 15%
        has_real_signal = any(s.signal != "unknown" for s in voting_sources)
        confidence = max(0.15 if has_real_signal else 0.05, confidence)
    else:
        confidence = 0.0

    # Wikidata veto: force toward REJECT
    if wikidata_veto:
        weighted_score = min(weighted_score, -0.5)

    # Recommendation
    if weighted_score >= ACCEPT_THRESHOLD:
        recommendation = "ACCEPT"
    elif weighted_score <= REJECT_THRESHOLD:
        recommendation = "REJECT"
    else:
        recommendation = "REVIEW"

    # Edge case: very low confidence overall → escalate to REVIEW
    if confidence < 0.10 and recommendation != "REJECT":
        recommendation = "REVIEW"

    narrative = _build_narrative(
        round(weighted_score, 4),
        recommendation,
        breakdown,
        [s for s in unknown_sources if s in WEIGHTS],
    )

    return ScorerResult(
        weighted_score=round(weighted_score, 4),
        confidence=confidence,
        recommendation=recommendation,
        narrative=narrative,
        source_breakdown=breakdown,
        unknown_sources=[s for s in unknown_sources if s in WEIGHTS],
    )


# ── Convenience: build SourceInput from raw source dicts ────────────────────

def source_input_from_dict(d: dict) -> SourceInput:
    """
    Convert the raw dict returned by any source module into a SourceInput.
    The 'source' key must match one of: gov_data, food_platforms, social_signal
    """
    return SourceInput(
        source=d.get("source", "unknown"),
        signal=d.get("signal", "unknown"),
        confidence=float(d.get("confidence", 0.0)),
        detail=d.get("detail"),
    )


# ── Quick test ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Simulate: gov says active, food platforms say closed, social unknown
    test_sources = [
        SourceInput("gov_data",       "active",  0.9,  "NEA licence active"),
        SourceInput("food_platforms", "closed",  0.85, "Burpple closed badge"),
        SourceInput("social_signal",  "unknown", 0.1,  "No Reddit posts found"),
    ]
    result = compute_score(test_sources)
    print(f"Score      : {result.weighted_score:+.4f}")
    print(f"Confidence : {result.confidence:.1%}")
    print(f"Verdict    : {result.recommendation}")
    print(f"Narrative  : {result.narrative}")
    print()

    # Simulate: all agree it's active
    test_sources_2 = [
        SourceInput("gov_data",       "active", 0.9, "NEA licence valid"),
        SourceInput("food_platforms", "active", 0.7, "Recent Burpple activity"),
        SourceInput("social_signal",  "active", 0.6, "Reddit post 1 month ago"),
    ]
    result2 = compute_score(test_sources_2)
    print(f"Score      : {result2.weighted_score:+.4f}")
    print(f"Confidence : {result2.confidence:.1%}")
    print(f"Verdict    : {result2.recommendation}")

    # Simulate: Wikidata dissolved (hard veto)
    test_sources_3 = [
        SourceInput("gov_data",       "closed", 0.95, "Wikidata dissolved 2023"),
        SourceInput("food_platforms", "active", 0.6,  "Old Burpple listing"),
        SourceInput("social_signal",  "unknown", 0.1, "No Reddit"),
    ]
    result3 = compute_score(test_sources_3)
    print(f"\nVeto test  : {result3.recommendation}  (score {result3.weighted_score:+.4f})")
