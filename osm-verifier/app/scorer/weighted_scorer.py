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

ACCEPT_THRESHOLD =  0.35
REJECT_THRESHOLD = -0.35


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

def _normalised_weight(source: str, effective_confidence: float) -> float:
    """
    Returns the base weight for a source scaled by the source's confidence.
    Sources that return 'unknown' still consume their base weight slot
    but do not contribute a vote (vote == 0).
    """
    return WEIGHTS.get(source, 0.0) * effective_confidence


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

    Handles:
    - Missing sources gracefully (treated as unknown, weight redistributed)
    - Conflicting signals (active vs closed) → REVIEW
    - Low total evidence (all unknown) → REVIEW with low confidence
    - Wikidata-dissolved veto: if gov_data is 'closed' with high confidence
      it acts as a hard floor pushing the score toward REJECT regardless of
      other signals.

    Returns a ScorerResult.
    """
    source_map: dict[str, SourceInput] = {s.source: s for s in sources}

    # Check for Wikidata veto (gov_data signals closed at high confidence)
    gov = source_map.get("gov_data")
    wikidata_veto = (
        gov is not None
        and gov.signal == "closed"
        and gov.confidence >= 0.85
    )

    weighted_sum = 0.0
    total_effective_weight = 0.0
    breakdown: list[dict] = []
    unknown_sources: list[str] = []

    for source_name, base_weight in WEIGHTS.items():
        src = source_map.get(source_name)

        if src is None:
            # Source not provided — treated as unknown, skip contribution
            unknown_sources.append(source_name)
            continue

        vote = SIGNAL_VOTE.get(src.signal, 0.0)
        effective_confidence = max(0.0, min(1.0, src.confidence))
        effective_weight = base_weight * effective_confidence

        weighted_sum += vote * effective_weight
        total_effective_weight += effective_weight

        breakdown.append({
            "source": source_name,
            "signal": src.signal,
            "vote": vote,
            "source_confidence": effective_confidence,
            "base_weight": base_weight,
            "effective_weight": round(effective_weight, 4),
            "detail": src.detail,
        })

        if src.signal == "unknown":
            unknown_sources.append(source_name)

    # Normalise score to [-1, +1] relative to maximum possible weight
    max_possible_weight = sum(WEIGHTS[s] for s in source_map)
    if max_possible_weight > 0:
        weighted_score = weighted_sum / max_possible_weight
    else:
        weighted_score = 0.0

    # Confidence = fraction of weight that produced a non-unknown signal
    non_unknown_weight = sum(
        WEIGHTS[s.source] * s.confidence
        for s in sources
        if s.signal != "unknown"
    )
    confidence = round(
        non_unknown_weight / sum(WEIGHTS.values()), 3
    )

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
    if confidence < 0.15 and recommendation != "REJECT":
        recommendation = "REVIEW"

    narrative = _build_narrative(
        round(weighted_score, 4),
        recommendation,
        breakdown,
        [s for s in unknown_sources if s in WEIGHTS],  # only real sources
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
