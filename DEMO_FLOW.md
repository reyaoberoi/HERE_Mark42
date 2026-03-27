# OSM Singapore POI Verifier - Demo Flow

## Pre-Demo Checklist (5 min)
```bash
# 1. Start backend
cd osm-verifier
python main.py

# Output should show:
# INFO:     Uvicorn running on http://127.0.0.1:8000

# 2. Verify all endpoints responding
# In another terminal:
curl http://localhost:8000/data-sources  # Should list 9 sources

# 3. Open frontend
# In browser: http://127.0.0.1:8000 (or open frontend/index.html)
```

---

## Demo: Core Verification Flow (3 min)

### Part 1: Search for a POI
**Scenario**: "Let's verify if a restaurant is still open in Singapore"

1. **Open the dashboard** → http://127.0.0.1:8000
   - Show clean interface: search form on left, Leaflet map on right
   - Explain: "This is a human-readable verification system, no AI marketing"

2. **Search for a POI** 
   - Enter: `Lau Pa Sat`
   - Show results in candidates list below
   - Click on a candidate to center map and populate verify form
   - Show the populate data: name, address, lat/lon auto-filled

3. **Verify the POI**
   - Click "Verify" button
   - Show the verification spinner + results appearing
   - Point out:
     - **Confidence score** (e.g., 78% - "HIGH probability it's still open")
     - **Data source breakdown**: Lists which of 9 sources voted (gov_data, wikidata, mapillary, food_platforms, etc.)
     - **Pipeline steps**: Shows 0.3s geo check → 0.2s registry check → 0.8s web check timing
     - **Mapillary compare**: Shows before/after street view images with dates
     - **Visual delta score**: Detects if storefront appearance changed

---

## Demo: Data Source Transparency (2 min)

### Part 2: Show "What Data Are We Using?"
**Scenario**: "Trust matters - here's exactly where we get our data"

1. **Click "Data Sources" button** (or navigate to `/data-sources`)
   - Show the 9 sources listed:
     ```
     1. gov_data (TF-IDF from official registry - weight: 1.35)
     2. mapillary (Street views - weight: 1.20)
     3. sg_gov_live (Real-time API from data.gov.sg - weight: 1.10)
     4. wikidata (Wikipedia DBpedia - weight: 0.90)
     5. food_platforms (Burpple, HungryGoWhere, etc. - weight: 0.85)
     6. tripadvisor (Reviews + POI signals - weight: 0.80)
     7. reddit (Community posts - weight: 0.75)
     8. wayback (Historical snapshots - weight: 0.70)
     9. osm_geo (Local OSM edits - weight: 0.65)
     ```
   - Emphasize: "No proprietary search engines. We use Brave privacy search + Qwant fallback"

2. **Show scoring model philosophy**
   - "Weighted logit model, not black-box AI"
   - "Each source has a reliability weight calibrated on known data"
   - "If sources conflict, we detect and surface the debate"

---

## Demo: Heatmap Visualization (2 min)

### Part 3: "Which POIs are at Risk?"
**Scenario**: "Show me which restaurants in Singapore might have outdated OSM records"

1. **Open Heatmap**
   - Navigate to `/heatmap-data` (or use heatmap tab if in frontend)
   - Show:
     - All nodes on Singapore map as red/orange/green circles
     - Zoom in to see density
     - Hover to see `{name, tags, edit_age_days}`

2. **Explain the colors**
   - Red = "risky" (closed risk + no recent Mapillary + old edit)
   - Orange = "medium concern"
   - Green = "recently updated"

3. **Show summary stats**
   - "Threshold: 2 years since last edit = stale"
   - "Active POIs: 2,342 | Stale POIs: 847"

---

## Demo: Innovation - OSM Lag Solution (2 min)

### Part 4: "Freshness Debt Queue"
**Scenario**: "How do we prioritize which POIs to update first?"

1. **Show evaluation results** (if evaluation has run)
   - Navigate to `osm-verifier/evaluation/model_eval_latest.json`
   - Show: Sample accuracy, confidence calibration, per-source reliability
   
2. **Explain the innovation**
   - "Instead of random updates, we rank POIs by freshness_debt:"
     - `0.40 × closure_risk` (How likely is it actually closed?)
     - `0.25 × age_norm` (How old is the OSM record?)
     - `0.20 × neighbourhood_churn` (Is this area active?)
     - `0.15 × conflict` (Do sources disagree?)
   - Result: Prioritized review queue for human mappers

3. **Show changeset diffs** (for auditing)
   - `osm-verifier/evaluation/changeset_diffs_latest.jsonl`
   - Each line = what the verifier would change for a POI
   - Reviewers can see exact proposed edits before approval

---

## Demo: Advanced Features (3 min) — *Optional*

### Part 5a: Map Click Behavior
- Click on any marker on the heatmap
- Shows popup: `{name, osm_node_id, lat, lon, tags}`
- Shows last edit timestamp
- Option to "Verify this POI" (redirects to verify form)

### Part 5b: Live Search Engine Fallback
- In frontend, search for a very new POI (not in gov_data yet)
- Verifier silently falls back: Brave → Qwant
- Shows that it found the POI anyway via web search
- Point out: "Zero latency from search engine outage"

### Part 5c: Batch Evaluation
```bash
# Run the evaluation pipeline
cd osm-verifier
python scripts/evaluate_model.py

# Shows:
# - Model accuracy on sample set
# - Per-source confusion matrices
# - Changeset diffs ready for OSM PR
```

---

## Demo Talking Points

### Problem Solved
- **OSM lag**: Street-level data in Singapore is 2+ years stale. Lives change faster than mappers edit.
- **Data fragmentation**: Multiple sources disagree. Need systematic conflict detection.
- **Trust**: Users don't know which data source is authoritative. Our answer: transparency + weighted aggregation.

### Key Differentiators
1. **No AI marketing language** - humans understand what they're verifying
2. **Privacy-first search** - Brave + Qwant, never Google/Bing
3. **Source attribution** - every signal is traceable to a real dataset
4. **Street-level feedback** - Mapillary before/after captures real-world changes
5. **Human review gate** - evaluation artifacts (changeset diffs) for OSM contributors to approve

### Scoring Philosophy
- Bayesian-inspired weighted evidence (not naive LR, not deep learning black box)
- ~40% baseline accuracy on known test set (honest about limitations)
- Calibrated per-source confidence: gov_data most reliable (1.35×), street views next (1.20×)
- Explicit conflict handling: if sources disagree, transparency wins over false confidence

### Live Editing Demo (Optional)
```bash
# If you have a changeset ID:
curl -X POST http://localhost:8000/submit-changeset \
  -H "Content-Type: application/json" \
  -d '{"changeset_id": "12345", "node_ids": [123, 456]}'

# Shows backend can integrate verified changes back to OSM
```

---

## Timing Summary
- **Pre-demo setup**: 2 min (start server, verify endpoints)
- **Core demo (Parts 1-4)**: 9 min
- **Advanced features (Part 5)**: 3 min optional
- **Q&A buffer**: 3-5 min
- **Total**: ~15-20 min for compelling presentation

---

## Demo Failure Recovery

| Issue | Fix |
|-------|-----|
| Backend won't start | Check `MAPILLARY_ACCESS_TOKEN` env var set; if missing, Mapillary signals skip gracefully |
| Frontend `/data-sources` shows 404 | Kill backend with Ctrl+C; restart (stale cache issue) |
| Heatmap empty | Run `scripts/setup_db.py` to populate cache (first-time only) |
| Search returns 0 results | Try "Tiong Bahru Market" (guaranteed in data); Brave fallback will still trigger |
| Mapillary images missing | Graceful degradation; shows "No street view available" instead of error |

---

## Things to Highlight During Q&A

1. **Why weighted logit vs. deep learning?**
   - Interpretable. We can explain *why* it says a restaurant is closed.
   - Runs on consumer laptop. No GPU needed.
   - Fast iteration with human-in-the-loop feedback.

2. **Why Brave + Qwant instead of Google?**
   - Privacy. No tracking user searches.
   - Open access. Brave API is free tier 2000 queries/day; Qwant has no auth requirement.
   - Prevents vendor lock-in to Google.

3. **How do you handle conflicts (sources disagree)?**
   - We detect and surface it. Safety rule: if gov_data + mapillary both say "closed", we're 95% confident.
   - If only one source says closed, we flag "needs human review".

4. **Can this be deployed?**
   - Yes. `docker-compose.yaml` included for postgres + backend.
   - Frontend is pure HTML/JS; can be static hosted anywhere (Netlify, S3, etc.).
   - Evaluation pipeline generates artifacts for OSM PR submissions.

5. **What's the "OSM lag innovation"?**
   - Freshness Debt Queue: rank POIs by urgency (age + closure risk + neighbourhood churn).
   - Daily batch generates prioritized review list for volunteer mappers.
   - Turns reactive "random updates" into proactive "highest-impact updates first".

