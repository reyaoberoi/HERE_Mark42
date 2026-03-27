# HERE Mark42: Architecture Brief (Judge-Focused)

## 1) Solution Architecture (What We Built)
Our system is a deterministic POI freshness engine for Singapore that verifies whether places are Open, Closed, or Review by combining:
- OSM geospatial truth (baseline location + tags)
- Government and public registry signals
- Web/platform signals (food/business directories)
- Social signals (recent mentions)
- Street-level visual change signals (Mapillary)

Pipeline design:
1. Geo resolve (OSM/Nominatim/Overpass)
2. Cross-check with structured registries
3. Web + visual evidence collection
4. Weighted scoring + contradiction detection
5. Outputs: recommendation, confidence, evidence trace, contradiction log

## 2) Why No LLM
We intentionally avoided LLM inference in the decision loop for reliability and reproducibility:
- Deterministic outputs for the same input
- Auditability (every source contribution is visible)
- Lower latency/cost for production-scale verification
- No hallucination risk in final status decision
- Easier governance for map-edit workflows

LLMs are optional for explanation UX, but not required for core correctness.

## 3) Uniqueness & Innovation
Key originality comes from multi-signal closure detection without restricted APIs:
- Visual temporal change (Mapillary before/after) as closure evidence
- Contradiction-first logging: detects when OSM says active but evidence says closed
- Tiered evidence strategy to reduce cost (cheap checks first, expensive checks later)
- Source-aware confidence weighting (not simple majority vote)
- Conservative Review state for conflicting evidence to avoid false edits

This enables new/closed-place detection using alternative, publicly available signals.

## 4) Technical Realization (Depth + Correctness)
Implemented stack includes:
- FastAPI backend with async source connectors
- Geospatial resolution and nearby context logic
- Weighted scorer with explicit priors and conflict handling
- SQLite cache for repeat-query efficiency
- JSON contradiction audit trail for traceability
- Frontend map visualization with evidence transparency

Why this is technically sound:
- Evidence is structured per-source (status/confidence/detail)
- Endpoints are reproducible and testable
- Cache versioning prevents stale-logic responses
- Contradictions are persisted and queryable

## 5) Scalability & Business Value
Scalability:
- Async IO allows concurrent source fetching
- Tiered pipeline controls compute/API cost
- Caching reduces repeated network calls
- Stateless API design supports horizontal scaling

Business value:
- Faster map freshness maintenance
- Better POI trust for navigation/search products
- Lower manual QA effort via contradiction triage
- Strong fit for location intelligence and marketplace ops

## 6) Presentation & Communication (Demo Narrative)
Recommended 90-second storyline:
1. Query a known place and show evidence-backed status
2. Show source-level transparency (not black-box output)
3. Show contradiction log update in real time
4. Explain deterministic scoring and why Review prevents wrong edits
5. Close with scalability: async + cache + tiered checks

## 7) Rubric Alignment Summary
- Uniqueness & Innovation: Multi-source closure detection + visual temporal change + contradiction logging
- Technical Realization: Deterministic async pipeline, geospatial + scoring + traceable outputs
- Scalability & Business Value: Tiered architecture, caching, operational map-maintenance value
- Presentation & Communication: Clear evidence-first demo with measurable impact
