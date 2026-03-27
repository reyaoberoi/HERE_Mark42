# HERE Mark42: Singapore OSM POI Freshness Verifier

Production-focused POI verification system for Singapore that checks whether an OSM place is still active, likely closed, or uncertain using multi-source evidence.

## What This Project Solves

OSM place data can lag behind real-world changes. Businesses close, move, or rebrand faster than map updates happen. This system:

- Resolves and verifies a place in Singapore
- Runs a multi-stage evidence pipeline (geo, registry, web, visual)
- Produces interpretable confidence and recommendation
- Flags high-confidence contradictions against current OSM tags
- Logs contradictions to a live JSON audit file
- Produces evaluation JSON artifacts for judging and model tracking

## Core Features

- Weighted evidence scoring (interpretable, not black-box)
- Strong visual signal handling via Mapillary
- Source-specific evidence output per place (not generic source list only)
- Contradiction detection and persistent logging
- Live map UI with cleaner marker styling and accurate coordinates
- Evaluation artifacts (`model_eval_latest.json`, `changeset_diffs_latest.jsonl`)

## Tech Stack

- Backend: FastAPI + Uvicorn
- Frontend: HTML + Vanilla JS + Leaflet
- DB: PostgreSQL/PostGIS (via Docker) or local PostgreSQL; SQLite fallback/cache
- Data/Search Sources:
  - OSM/Nominatim/Overpass (`osm_geo`)
  - Local SG gov mirror (`gov_data`)
  - data.gov.sg live APIs (`sg_gov_live`)
  - Food platform scraping + Brave/Qwant fallback (`food_platforms`)
  - TripAdvisor typeahead/fallback (`tripadvisor`)
  - Wayback CDX + live probe (`wayback`)
  - Wikidata SPARQL (`wikidata`)
  - Mapillary visual-change analysis (`mapillary`)

## Project Structure

- `osm-verifier/main.py`: API entrypoint and pipeline orchestration
- `osm-verifier/app/sources/`: all source connectors
- `frontend/index.html`: UI and map
- `osm-verifier/contradictions/live_contradictions.json`: live contradiction audit log
- `osm-verifier/evaluation/model_eval_latest.json`: latest evaluation metrics
- `osm-verifier/evaluation/changeset_diffs_latest.jsonl`: proposed OSM diff artifacts
- `scripts/setup_db.py`: database bootstrap and data loading

## Setup (Windows + Docker Desktop)

### 1. Start database from project root

```powershell
cd "C:\Users\Ayush Manoj Garg\OneDrive\Desktop\mpstme\here tech hackathon\HERE_Mark42"
docker-compose up -d
docker ps
```

### 2. Initialize DB and load OSM data

```powershell
python scripts/setup_db.py
```

### 3. Start backend from `osm-verifier`

```powershell
cd osm-verifier
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

### 4. Open app

- `http://127.0.0.1:8000`

## If Port 8000 Is Busy

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen |
Select-Object -First 1 -ExpandProperty OwningProcess |
ForEach-Object { Stop-Process -Id $_ -Force }
```

Then start Uvicorn again.

## Key API Endpoints

- `GET /health`: service status + heatmap node count
- `GET /data-sources`: source transparency and policy
- `GET /search?q=<place>`: candidate retrieval with coordinates and tags
- `POST /verify`: full verification pipeline response
- `GET /heatmap-data`: risk nodes + summary
- `GET /contradictions`: current contradiction records from JSON

## Verification Output Highlights (`POST /verify`)

Important fields for judging:

- `predicted_status`: `Established`, `Recently Closed`, `Uncertain`, `New Place`
- `recommendation`: `ACCEPT`, `REVIEW`, `REJECT`
- `confidence`: certainty of final classification
- `matched_sources`, `matched_source_count`: only place-relevant source evidence
- `confidence_formula`: short explanation of score components
- `contradiction_flag`, `contradiction_recorded`: contradiction tracking status
- `pipeline_steps`: stage-wise execution timing

## Contradiction Logging (Live JSON)

File: `osm-verifier/contradictions/live_contradictions.json`

A record is added only when all are true:

- prediction is not uncertain,
- recommendation is decisive (`REJECT` in contradiction flow),
- current OSM implies active but pipeline strongly indicates closure.

This avoids noisy logs from weak/uncertain outcomes.

## Evaluation Artifacts

Run evaluator:

```powershell
cd osm-verifier
.\venv\Scripts\python.exe scripts\evaluate_model.py
```

Generated artifacts:

- `osm-verifier/evaluation/model_eval_latest.json`
- `osm-verifier/evaluation/changeset_diffs_latest.jsonl`
- timestamped historical files (for comparison over iterations)

Use these in your demo to show reproducibility and metric tracking.

## Judge Demo Path (Quick)

Use `DEMO_FLOW.md` as the exact narration script.

## Notes

- No Google/Bing search is used in runtime search fallback.
- Current runtime avoids Playwright/Chromium browser automation path.
- Mapillary token should be set in `osm-verifier/.env` for best visual signals.

## Environment File

Copy and edit:

```powershell
copy osm-verifier\.env.example osm-verifier\.env
```

Critical vars:

- `MAPILLARY_ACCESS_TOKEN`
- `BRAVE_SEARCH_API_KEY` (optional but recommended)
- `DATABASE_URL` (if using local PostgreSQL)

## Final Hackathon Checklist

- Docker DB container running
- API healthy (`/health`)
- Search and verify working in UI
- Contradiction JSON updates after a decisive contradiction case
- Evaluation JSON artifacts present and explainable
