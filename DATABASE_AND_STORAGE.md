# Database & Storage Architecture - Complete Explanation

## TL;DR Answers to Your Questions

### ❓ "Model evaluation doesn't change when I change the place to be queried"
**Root Cause**: The static `evaluate_model.py` script evaluates the same 5 hardcoded test places every time.

**Solution**: Use the new `/evaluate-live` endpoint (called after `/verify`) which gives you real-time evaluation metrics for the CURRENT place you just queried.

**How to use**:
```bash
# 1. Call verify
POST /verify
{
  "name": "Your Place",
  "address": "Your Address"
}

# 2. Then call evaluate-live  
POST /evaluate-live
{
  "name": "Your Place",
  "address": "Your Address"
}

# Returns: Real-time metrics for that specific place (confidence%, recommendation, sources, etc.)
```

---

### ❓ "Why does contradictions work for Burger & Lobster only?"
**Root Cause**: NOT hardcoded. Contradictions ONLY appear when a place meets ALL 4 criteria:
1. Place found in OSM (`osm_found = true`)
2. Predicted status is "Recently Closed" (`p_closed >= 0.62`)
3. Recommendation is "REJECT" (high confidence closure)
4. No conflict between active and closed signals

**Why B&L qualifies**: Has Mapillary CLOSED evidence (visual change 2017→2019) + OSM marks it ACTIVE = Clear contradiction

**Why other places don't**: Most don't have strong enough closure evidence (Mapillary images, gov data confirmation, etc.) so they stay in "REVIEW" status instead of "REJECT"

**Solution**: The conditions are NOT hard-coded, they're generic. When you query a place that DOES have strong closure evidence, it WILL appear in contradictions.json and be shown via `/contradictions` endpoint.

**How contradictions update**:
- Query Place A → If meets criteria → Added to live_contradictions.json
- Query Place B → If meets criteria → Also added to live_contradictions.json
- Query Burger & Lobster again → Same entry deduplicated (only 1 record per place)

---

### ❓ "Attributes shown below don't match attributes in verify box - fix that"
**Root Cause**: Frontend was only displaying a subset of available attributes. Response had 30+ fields, frontend showed ~10.

**Solution**: Updated frontend to display ALL attributes in organized sections:

**Before** (incomplete):
```
confidence, recommendation, predicted_status, matched_source_count, matched_sources, 
confidence_formula, contradiction_flag, contradiction_recorded
```

**After** (complete with all 30+ fields from VerifyResponse):
```
=== CORE PREDICTION ===
- place_name, predicted_status, recommendation, confidence, osm_found, osm_id, location

=== SOURCE EVIDENCE ===
- matched_source_count, matched_sources, considered_sources, active_sources, closure_sources

=== MODEL INTERNALS ===
- confidence_formula, conflict_flag, contradiction_flag, contradiction_recorded

=== MAPILLARY VISUAL SIGNAL ===
- visual_delta_score, change_class, mapillary_before_date, mapillary_after_date

=== STALENESS & FRESHNESS ===
- edit_age_days, neighbourhood_activity_score, prior_p_active

=== NARRATIVE ===
- narrative (2-sentence explanation of the prediction)
```

---

### ❓ "Where in the database is it changing? Fully explain."
**This is the KEY question** — Here's the exact flow:

## Data Storage Architecture

### Layer 1: SQLite Cache DB (`cache.db`)
**Location**: `osm-verifier/cache.db`
**What**: Caches verification results to avoid redundant computation
**Table**: `verify_cache`
**Fields**:
```sql
key TEXT PRIMARY KEY       -- SHA256(place_name|address) hash
result TEXT                -- Full VerifyResponse JSON serialized
created_at TEXT            -- ISO timestamp when cached
__schema_version INT       -- Incremented when response structure changes
```

**When it updates**: 
1. You call `/verify` with place name + address
2. Backend creates cache key: `SHA256("burger & lobster".lower() | "address".lower())`
3. Checks `cache.db` for this key
4. If found AND `created_at < now - 24 hours` → Use cached result (INSTANT)
5. If not found OR expired → Run full pipeline (20-50 seconds)
6. After pipeline completes → **WRITE to cache.db** with timestamp

**Example after querying "Burger & Lobster"**:
```
cache.db verify_cache table:
├─ key: 6a7f8e9d2c1b... (SHA256 hash)
├─ result: {"place_name": "Burger & Lobster", "recommendation": "REJECT", ...}
└─ created_at: 2026-03-27T07:20:46.412927+00:00
```

**TTL**: 24 hours (automatically expires old cache)

**Query count**: Roughly 1-5 places queried = 1-5 rows in cache.db

---

### Layer 2: Contradictions JSON (`live_contradictions.json`)
**Location**: `osm-verifier/contradictions/live_contradictions.json`
**What**: Audit log of discovered contradictions between OSM and other signals
**Format**: JSON array (max 500 records, newest first)
**Fields per record**:
```json
{
  "place_name": "Burger & Lobster",
  "osm_id": "6956299310",
  "lat": 1.3604266,
  "lon": 103.9900241,
  "recommendation": "REJECT",
  "predicted_status": "Recently Closed",
  "confidence": 76,
  "matched_source_count": 2,
  "matched_sources": [
    {"source": "osm_geo", "status": "ACTIVE", ...},
    {"source": "mapillary", "status": "CLOSED", ...}
  ],
  "confidence_formula": "base_prior=0.75; active=1; closed=1; ...",
  "dedupe_key": "6956299310|Burger & Lobster|Recently Closed",
  "created_at": "2026-03-27T07:20:46.412927+00:00"
}
```

**When it updates**:
1. You call `/verify` on a place
2. Pipeline runs and produces: `predicted_status`, `recommendation`, `confidence`, etc.
3. Scoring logic sets `contradiction_flag = true` IF:
   - `osm_found == true` (place is in OSM)
   - `predicted_status == "Recently Closed"` (p_closed >= 0.62)
   - `recommendation == "REJECT"` (high confidence)
   - `conflict_flag == false` (not mixed active+closed)
4. If ALL conditions met → **APPEND to live_contradictions.json**
5. Uses `dedupe_key` to prevent duplicates

**Deduplication logic**:
```
dedupe_key = "{osm_id}|{place_name}|{predicted_status}"
If key already exists in JSON → Skip adding (prevents duplicates)
```

**Example after querying Burger & Lobster**:
```
live_contradictions.json:
[
  {
    "place_name": "Burger & Lobster",  ← Most recent
    "recommendation": "REJECT",
    ...
  },
  // ... older entries if you queried other contradicting places
]
```

**Why Burger & Lobster is there**: Has Mapillary CLOSED (visual evidence) + OSM marked ACTIVE = Clear contradiction

**Read it via**: GET `/contradictions` endpoint or read file directly

---

### Layer 3: Model Evaluation (`model_eval_latest.json`)
**Location**: `osm-verifier/evaluation/model_eval_latest.json`
**What**: Expected vs actual predictions against test samples
**Generated by**: `python scripts/evaluate_model.py`
**Frequency**: MANUAL (not automatic)
**Test samples from**: `scripts/evaluation_samples.json` (hardcoded 5 places)

**IMPORTANT**: This is STATIC until you manually run the evaluation script. It does NOT update per query.

**To update it, run**:
```bash
cd osm-verifier
python scripts/evaluate_model.py
```

**Then check**: `evaluation/model_eval_latest.json` for updated accuracy metrics

---

### Layer 4: Heatmap Data (`heatmap.json`)
**Location**: `osm-verifier/heatmap.json`
**What**: Pre-computed staleness risk scores for all Singapore POIs
**Records**: ~2000+ POIs with risk scores
**Generated by**: `python build_stats.py`
**Used for**: Prior probability and neighbourhood context in scoring

---

## Complete Data Flow Diagram

```
┌─ YOU CALL /verify ─────────────────────────────────────────┐
│  POST /verify { "name": "Burger & Lobster", "address": ... }│
└──────────────────────────┬────────────────────────────────┘
                           │
                           ▼
        ┌─ Check cache.db ─────────────────┐
        │ key = SHA256(name|address)       │
        │ Is it in cache AND not expired?  │
        │                                  │
        │ YES → Return cached result       │ (INSTANT)
        │ NO  → Continue to pipeline       │ (20-50 seconds)
        └──────┬──────────────────────────┘
               │
               ▼
    ┌─ Run Tier 1, 2, 3 Pipeline ─────────┐
    │ - Geocoding (OpenStreetMap)         │
    │ - Gov data, Wikidata, etc (Tier 2) │
    │ - Web, visual signals (Tier 3)      │
    └─────────┬──────────────────────────┘
              │
              ▼
    ┌─ Compute Score ──────────────────────────┐
    │ Input: All source signals                │
    │ Output: recommendation, confidence, etc  │
    │                                           │
    │ Sets: contradiction_flag = ?              │
    │   IF osm_found AND Recently Closed        │
    │   AND REJECT AND not conflict             │
    │   THEN contradiction_flag = true          │
    └────────┬──────────────────────────────┘
             │
             ├─ WRITE to cache.db ─────────────────║
             │  (INSERT/UPDATE verify_cache table)  ║
             │                                      ║
             └─ IF contradiction_flag == true ─────╫─► APPEND to live_contradictions.json
                  (deduplicate by key)              ║
                                                   ║
               ┌─ Return VerifyResponse ──────────╫─► HTTP 200 with all 30+ fields
               │ - place_name                     ║
               │ - predicted_status               ║
               │ - recommendation                 ║
               │ - confidence                     ║
               │ - edit_age_days                  ║
               │ - neighbourhood_activity_score   ║
               │ - etc...                         ║
               └─────────────────────────────────┘║
                                                  ║
        Frontend receives full response ◄─────────┘

    Frontend displays:
    ├─ Core prediction (name, status, recommendation, confidence)
    ├─ Source evidence (matched sources, active/closure sources)
    ├─ Model internals (formula, conflict, contradiction flags)
    ├─ Mapillary visual signal (if available)
    ├─ Staleness info (edit age, neighbourhood score)
    └─ Plus: Calls /contradictions to show audit log
```

---

## Real-Time Verification Example

### Scenario: You query "Burger & Lobster"

```
1. TIME: 0s
   You type: "Burger & Lobster"
   You click: "Verify" button
   
2. TIME: ~0.1s
   Backend: Check cache.db for SHA256("burger & lobster|address")
   Result: Not found (or expired)
   Decision: Run full pipeline
   
3. TIME: 0.2s - 30s
   Tier 1: OSM geocoding → finds osm_id=6956299310
   Tier 2: Gov data, Wikidata, SG gov API
   Tier 3: Mapillary visual, Wayback, TripAdvisor
   
   Mapillary result: 
   ├─ before_date: 2017-01-13
   ├─ after_date: 2019-08-14  
   ├─ status: CLOSED
   ├─ confidence: 0.65
   └─ change_class: "Major visual change"
   
4. TIME: 30s - 31s
   Scoring: Compute posterior probability
   │ OSM says: ACTIVE (confidence 0.55)
   │ Mapillary says: CLOSED (confidence 0.65, weight 1.55x)
   │ Result: p_closed = 0.76, p_active = 0.24
   │ recommendation = "REJECT"
   │ predicted_status = "Recently Closed"
   │ confidence = 76%
   │
   Contradiction check:
   │ osm_found? YES ✓
   │ predicted_status == "Recently Closed"? YES ✓
   │ recommendation == "REJECT"? YES ✓
   │ conflict_flag == false? YES ✓
   │ → contradiction_flag = TRUE
   
5. TIME: 31s - 32s
   DATABASE WRITES:
   
   a) Write to cache.db:
      INSERT/UPDATE verify_cache SET
      key = '6a7f8e...',
      result = '{full JSON response}',
      created_at = '2026-03-27T07:20:46Z',
      __schema_version = 6
      
   b) Write to live_contradictions.json:
      Check dedupe_key = "6956299310|Burger & Lobster|Recently Closed"
      Not yet in file? 
      → APPEND new record to front of array
      → File now has:
         [{Burger & Lobster}, ... other previous contradictions]
      
6. TIME: 32s
   Return HTTP 200 response with:
   {
     "place_name": "Burger & Lobster",
     "lat": 1.3604266,
     "lon": 103.9900241,
     "osm_id": "6956299310",
     "predicted_status": "Recently Closed",
     "recommendation": "REJECT",
     "confidence": 76,
     "edit_age_days": (calculated),
     "neighbourhood_activity_score": (from heatmap),
     "visual_delta_score": 0.12,
     "change_class": "Major visual change",
     "matched_sources": [osm_geo, mapillary],
     "contradiction_flag": true,
     "contradiction_recorded": true,
     ... (28 more fields)
   }
   
7. TIME: 32s
   Frontend displays:
   ├─ Summary: "Predicted Status: Recently Closed | Confidence: 76%"
   ├─ Recommendation: REJECT (red)
   ├─ All 30+ attributes in organized sections
   └─ Contradiction audit log: Shows Burger & Lobster as latest entry
```

---

## Why Only Burger & Lobster Appears in Contradictions

Hypothetical other places you might query:

```
Place          | osm_found | p_closed | recommendation | conflict | → Logged?
─────────────────────────────────────────────────────────────────────────────
Burger & Lobs. |    Y      |  0.76    |   REJECT       |    N     → YES ✓
Starbucks      |    Y      |  0.45    |   REVIEW       |    N     → NO (p_closed too low)
Old Airport Rd |    Y      |  0.68    |   REJECT       |    Y     → NO (conflict=true)
Don Don Donki  |    Y      |  0.72    |   REJECT       |    N     → YES ✓ (if queried)
New unknown    |    N      |    -     |   N/A          |    -     → NO (not in OSM)
```

**Key point**: When you query other places with strong closure signals AND they're in OSM AND no conflict, they'll ALSO appear in contradictions.json

---

## How to Query for Real-Time Results

### Get Live Evaluation (NEW):
```bash
curl -X POST http://localhost:8000/evaluate-live \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Your Place",
    "address": "Your Address"
  }'
```

Response includes:
- Real-time metrics for this query
- Storage locations 
- Cache TTL info
- Contradiction status

### Get Contradiction Audit Log (ENHANCED):
```bash
curl http://localhost:8000/contradictions
```

Response includes:
- Audit log items
- Storage file path
- Explanation of how contradictions work
- Why only certain places appear

### Get Storage & Database Info:
```bash
curl http://localhost:8000/storage-info
```

Response includes:
- All 4 storage layer details
- Complete data flow diagram
- Where each piece of data is stored
- When data updates

---

## Summary

| Question | Answer |
|----------|--------|
| Where is data stored? | 4 layers: cache.db (SQLite), live_contradictions.json (JSON file), heatmap.json (JSON), model_eval_latest.json (JSON) |
| Does evaluation update per query? | YES, use NEW `/evaluate-live` endpoint (was static before) |
| Why only Burger & Lobster? | Not hardcoded. Only appears when all 4 criteria met: osm_found + Recently Closed + REJECT + no conflict. Other places will appear when you query them if they meet criteria. |
| What attributes should show? | All 30+ fields from VerifyResponse. Frontend NOW shows all organized by section. |
| Does it write to database? | YES: cache.db (SQLite) on every verify, + live_contradictions.json (JSON) when contradiction criteria met |

