# OSM Lag Innovation: Singapore Freshness Debt Queue

## Problem
OSM lag happens when real-world business status changes faster than map updates.

## Solution Added
We introduce a **Freshness Debt Queue** to prioritize which POIs should be reviewed first.

A POI is assigned debt based on:
1. `closure-risk` from source fusion model
2. `edit-age` from OSM timestamp
3. `neighbourhood churn` from SG heatmap risk context
4. `source disagreement` (active vs closed conflict)

A simple score:

`freshness_debt = 0.40*risk + 0.25*age_norm + 0.20*churn + 0.15*conflict`

where each input is normalized to [0, 1].

## Why this helps in practice
- Routes human validation effort to highest-impact stale POIs.
- Reduces wasted checks on already stable POIs.
- Creates an auditable backlog for OSM update operations.

## Operational Flow
1. Run `scripts/evaluate_model.py` to generate model outputs and changeset diffs.
2. Build a debt-ranked list from the latest evaluation output.
3. Push top-N daily candidates to internal QA/review workflow.
4. Apply reviewed changesets to OSM with human approval.

## Artefacts already generated
- `evaluation/model_eval_latest.json`
- `evaluation/changeset_diffs_latest.jsonl`

These files can be used as the base for daily debt ranking automation.
