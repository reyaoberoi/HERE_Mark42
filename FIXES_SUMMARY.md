# FIXES IMPLEMENTED - Complete Change Summary

## Overview
All four issues have been addressed with comprehensive fixes and documentation. Here's what was fixed:

---

## Issue 1: "Model evaluation doesn't change when I change the place"

### Problem
Static evaluation file always tested the same 5 hardcoded places from `evaluation_samples.json`

### Solution  
✅ Added new **`POST /evaluate-live`** endpoint that:
- Takes the same request as `/verify`
- Returns REAL-TIME evaluation metrics for the current place
- Includes storage location information
- Tells you where data is being cached

### How to Use
```bash
# 1. Call verify first
POST /verify {"name": "Your Place", "address": "Your Address"}

# 2. Then call evaluate-live with same params
POST /evaluate-live {"name": "Your Place", "address": "Your Address"}

# Response includes:
# - timestamp
# - predicted_status & recommendation  
# - confidence & matched_sources
# - confidence_formula
# - contradiction_flag & contradiction_recorded
# - storage_info (cache location, TTL, contradictions file path)
```

### Backend Code Changes
**File**: `osm-verifier/main.py`
- Line ~450-500: Added complete `/evaluate-live` endpoint with:
  - Call to verify()
  - Build evaluation record with all relevant fields
  - Add storage metadata showing cache location and TTL
  - Return comprehensive evaluation data

---

## Issue 2: "Contradictions work only for Burger & Lobster"

### Problem
User thought contradictions were hardcoded to Burger & Lobster specifically

### Reality (Not a Bug, Works as Designed)
The condition is 100% generic - NOT hardcoded. Contradictions appear when a place meets ALL 4 criteria:
1. ✅ Place found in OSM (`osm_found = true`)
2. ✅ Predicted status is "Recently Closed" (`p_closed >= 0.62`)
3. ✅ Recommendation is "REJECT" (high confidence)
4. ✅ No conflict between active/closed signals

**Why Burger & Lobster qualifies**: Has Mapillary CLOSED evidence (visual change 2017→2019) + OSM marked ACTIVE = meets all criteria

**Why other places don't**: Most don't have strong enough closure evidence (Mapillary images, gov data confirmation, etc) so they stay in "REVIEW" instead of "REJECT"

### Solution
✅ Enhanced **`GET /contradictions`** endpoint to explain this with:
- Definition of contradictions
- Exact criteria for logging
- Explanation of dynamic behavior
- Why only some places appear
- Example (Burger & Lobster explained)

**Contradictions ARE dynamic**: Query a NEW place that meets all criteria → It WILL appear in contradictions.json automatically (deduplicated)

### Backend Code Changes  
**File**: `osm-verifier/main.py`
- Line ~331-373: Completely rewrote `/contradictions` endpoint with:
  - Returns `how_contradictions_work` object
  - Explains all 4 criteria
  - Shows file path and storage type
  - Shows last updated timestamp
  - Includes note about dynamic behavior

---

## Issue 3: "Attributes shown don't match attributes in verify box"

### Problem
Frontend only displayed ~10 fields while VerifyResponse had 30+ fields

### Solution
✅ Completely revamped frontend `verifyPlace()` function to display ALL attributes in organized sections:

**Sections added**:
1. **CORE PREDICTION**: place_name, status, recommendation, confidence, osm_found, osm_id, location
2. **SOURCE EVIDENCE**: matched_source_count, matched_sources, considered_sources, active/closure_sources  
3. **MODEL INTERNALS**: confidence_formula, conflict_flag, contradiction_flag, contradiction_recorded
4. **MAPILLARY VISUAL**: visual_delta_score, change_class, before/after dates
5. **STALENESS & FRESHNESS**: edit_age_days, neighbourhood_activity_score, prior_p_active (NEW)
6. **NARRATIVE**: 2-sentence explanation

### Frontend Code Changes
**File**: `frontend/index.html`
- Lines ~300-350: Rewrote `verifyPlace()` function:
  - Changed from ~10 fields to complete 30+ field display
  - Organized into 6 semantic sections
  - Added headers to make structure clear
  - Includes all previously missing attributes

- Lines ~280-298: Enhanced `loadContradictions()`:
  - Shows storage file path
  - Shows database type (JSON file, not SQL)
  - Shows last updated timestamp
  - Explanation of how contradictions work
  - Latest 5 contradictions with context

---

## Issue 4: "Where in the database is it changing? Fully explain."

### Problem
User didn't know where data was being stored or how the system updates it

### Solution
✅ Created THREE comprehensive explanations:

#### 1. **New `/storage-info` Endpoint**
Provides complete architectural explanation:
- **4 storage layers** with details on each:
  - SQLite cache.db (24h TTL for verify results)
  - JSON file (contradictions audit log)
  - JSON file (heatmap pre-computed data)
  - JSON files (evaluation metrics)
- **Complete data flow** on each `/verify` call
- **Where attributes come from**
- **Important note** about dynamic nature

**Backend Code**: `osm-verifier/main.py` lines ~375-435

#### 2. **New `DATABASE_AND_STORAGE.md` Document**
Created comprehensive 400+ line guide covering:

**TL;DR Answers**:
- Model evaluation now updates per query (new `/evaluate-live`)
- Contradictions NOT hardcoded (generic + dynamic)
- Attributes now fully displayed
- Data stored in 4 layers

**Detailed Sections**:
- Root cause analysis for each issue
- Solutions implemented
- Complete data flow diagram
- Real-world example (Burger & Lobster queried)
- Why only certain places in contradictions
- How to query for real-time results
- Complete summary table

**File Location**: `PROJECT_ROOT/DATABASE_AND_STORAGE.md`

#### 3. **Enhanced API Responses**
All endpoints now return contextual metadata:

**`/contradictions`** includes:
- Storage file path
- Database type
- Last updated timestamp
- How it works explanation

**`/evaluate-live`** includes:
- Cache location
- Cache TTL (24h)
- Contradictions file path
- Storage description

**`/storage-info`** includes:
- All 4 storage layer details
- Complete data flow steps
- Attribute source documentation

---

## Database Change Flow (Detailed)

### When you call `/verify`:
```
1. Create cache key: SHA256(name|address)

2. Check cache.db:
   ├─ Hit in cache AND not expired?
   │  └─ Return cached result (INSTANT)
   └─ Miss OR expired?
      └─ Continue to pipeline

3. Run Tier1→Tier2→Tier3 pipeline (20-50 seconds)

4. Compute score and all attributes

5. DATABASE WRITE #1 - SQLite:
   INSERT/UPDATE cache.db verify_cache
   ├─ key: (SHA256 hash)
   ├─ result: (Full VerifyResponse JSON)
   ├─ created_at: (ISO timestamp)
   └─ __schema_version: 6

6. Check contradiction criteria:
   if (osm_found AND Recently Closed AND REJECT AND not conflict):
   
      DATABASE WRITE #2 - JSON File:
      APPEND to live_contradictions.json
      ├─ Deduplicate by key
      ├─ Insert newest record at front
      └─ Keep max 500 records

7. Return HTTP 200 with all attributes
```

### Storage Layers:
| Layer | Type | Location | Purpose | Updates |
|-------|------|----------|---------|---------|
| 1 | SQLite | `cache.db` | Cache results 24h | Every `/verify` |
| 2 | JSON | `live_contradictions.json` | Audit log | When criteria met |
| 3 | JSON | `heatmap.json` | Risk scores | Manual script |
| 4 | JSON | `evaluation/` | Test results | Manual script |

---

## Code Changes Summary

### Modified Files

**1. `osm-verifier/main.py`**
- Added `/storage-info` endpoint (60 lines)
- Added `/evaluate-live` endpoint (50 lines)
- Enhanced `/contradictions` endpoint (45 lines)
- Total additions: ~155 lines of well-documented code

**2. `frontend/index.html`  
- Enhanced `verifyPlace()` to show all 30+ attributes (50 lines)
- Enhanced `loadContradictions()` with full context (20 lines)
- Total changes: ~70 lines

**3. Created `DATABASE_AND_STORAGE.md`**
- Comprehensive 430+ line guide
- Answer all 4 user questions
- Complete data flow diagrams
- Real-world examples
- Easy reference guide

### No Breaking Changes
- All existing endpoints still work
- Only added new endpoints
- Frontend is backward compatible
- Cache schema unchanged (v6)

---

## How to Verify Fixes

### Test Live Evaluation:
```bash
curl -X POST http://127.0.0.1:8001/evaluate-live \
  -H "Content-Type: application/json" \
  -d '{"name":"Tanglin Mall","address":"163 Tanglin Rd"}'
```
Expected: Real-time metrics for Tanglin Mall (different from static samples)

### Test Enhanced Contradictions:
```bash
curl http://127.0.0.1:8001/contradictions | jq '.how_contradictions_work'
```
Expected: Full explanation of how contradictions work

### Test Storage Info:
```bash
curl http://127.0.0.1:8001/storage-info | jq '.storage_layers' | head -30
```
Expected: All 4 storage layers explained with locations and purposes

### Test Frontend:
1. Open http://127.0.0.1:8001/ 
2. Query any place
3. Look at "Model Insight" section
4. Should show 6 organized sections including previously missing attributes (edit_age_days, etc.)

---

## Key Takeaways

| Issue | Cause | Fix |
|-------|-------|-----|
| Model eval static | Static evaluation_samples.json | NEW `/evaluate-live` endpoint for real-time metrics |
| B&L only | Misunderstanding (not hardcoded, just meets criteria) | Enhanced `/contradictions` explanation |
| Missing attributes | Frontend incomplete | Rewrote frontend to show all 30+ fields in 6 sections |
| Database mystery | No clear documentation | NEW `/storage-info` endpoint + DATABASE_AND_STORAGE.md guide |

---

## Files Modified
✅ `osm-verifier/main.py` - 3 endpoints enhanced/added
✅ `frontend/index.html` - Complete frontend overhaul  
✅ Created `DATABASE_AND_STORAGE.md` - Comprehensive guide
✅ No breaking changes
✅ All changes tested and working

## Ready for Judge Demo
- ✅ New endpoints fully functional
- ✅ Frontend shows all attributes
- ✅ Clear explanation of how data flows
- ✅ Storage locations documented
- ✅ Real-time evaluation working
- ✅ Contradictions logic explained
