# Judge Demo Flow (Live End-to-End)

This is the exact script to run in front of judges: live app, live contradiction update, and evaluation metrics artifacts.

## Demo Length

- Setup: 2-3 min
- Live demo: 8-10 min
- Evaluation + Q&A: 4-6 min
- Total: 15-20 min

## 0. Pre-Demo Setup (Do This Before Judges Arrive)

### Terminal A (Project root)

```powershell
cd "C:\Users\Ayush Manoj Garg\OneDrive\Desktop\mpstme\here tech hackathon\HERE_Mark42"
docker-compose up -d
python scripts/setup_db.py
```

### Terminal B (`osm-verifier`)

```powershell
cd "C:\Users\Ayush Manoj Garg\OneDrive\Desktop\mpstme\here tech hackathon\HERE_Mark42\osm-verifier"
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

### If port conflict occurs

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen |
Select-Object -First 1 -ExpandProperty OwningProcess |
ForEach-Object { Stop-Process -Id $_ -Force }
```

### Start API

```powershell
.\venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

### Open browser

- `http://127.0.0.1:8000`

## 1. Opening Narrative (30-45 sec)

Say this:

- "This system detects OSM lag in Singapore POIs by combining registry, web, and visual evidence."
- "We output explainable confidence, per-place source evidence, and a contradiction audit JSON for mapper review."

## 2. Live Pipeline Demo (Search -> Verify -> Explain)

### In UI

1. Search a known place (example: `Tanglin Mall` or `Lau Pa Sat`)
2. Click a candidate in the list
3. Show map pin and popup with coordinates/tags
4. Click Verify

### What to point out in result panel

- `predicted_status`
- `recommendation`
- `confidence`
- `matched_sources` and `matched_source_count`
- `confidence_formula`
- `pipeline_steps` timing

Script line:

- "We show only matched source evidence for this place, not a generic source dump."

## 3. Contradiction Demo (Most Important Part)

Use the case that usually contradicts stale OSM tagging:

- `Burger & Lobster`, address `Singapore`

### In UI

1. Enter place and verify
2. Show decisive outcome (`Recently Closed` / `REJECT` when contradiction is strong)
3. Explain this means evidence conflicts with stale OSM activity tags

### In Terminal C (live API proof)

```powershell
$body = @{name='Burger & Lobster'; address='Singapore'} | ConvertTo-Json
Invoke-WebRequest -UseBasicParsing -Method Post -Uri http://127.0.0.1:8000/verify -ContentType 'application/json' -Body $body | Select-Object -ExpandProperty Content
```

Call out fields in response:

- `contradiction_flag`
- `contradiction_recorded`
- `matched_sources`

## 4. Show Live JSON Update (Critical Judge Moment)

### Command

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/contradictions | Select-Object -ExpandProperty Content
```

### File proof

Open file in editor:

- `osm-verifier/contradictions/live_contradictions.json`

Say this:

- "A new entry is appended only for decisive contradictions, not uncertain cases."
- "This gives mappers a clean actionable queue instead of noisy alerts."

## 5. Show Evaluation Metrics Artifacts

### Run evaluator (if needed)

```powershell
cd "C:\Users\Ayush Manoj Garg\OneDrive\Desktop\mpstme\here tech hackathon\HERE_Mark42\osm-verifier"
.\venv\Scripts\python.exe scripts\evaluate_model.py
```

### Show files

- `osm-verifier/evaluation/model_eval_latest.json`
- `osm-verifier/evaluation/changeset_diffs_latest.jsonl`

Explain:

- `model_eval_latest.json`: latest run metrics and prediction quality summary
- `changeset_diffs_latest.jsonl`: per-place proposed tag changes for audit/review

Judge line:

- "We are not just predicting; we generate auditable change artifacts for human-in-the-loop validation."

## 6. Data Source Transparency Moment

Command:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/data-sources | Select-Object -ExpandProperty Content
```

Say:

- "These are the exact sources used; output is source-attributed and traceable."

## 7. Clean Closing Statement (20 sec)

- "This pipeline turns OSM freshness from reactive map edits into measurable, auditable, and prioritizable operations."
- "Live contradiction JSON plus evaluation artifacts make it deployable for mapper workflows, not just a one-off demo."

## Fallback Plan (If Something Breaks)

### App not loading

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/health | Select-Object -ExpandProperty Content
```

If not healthy, restart Uvicorn.

### Port already in use

Kill port 8000 process, then restart Uvicorn.

### No Mapillary data

- Continue demo with other sources and contradiction workflow.
- Mention token/config dependency for visual evidence.

## One-Page Command Block (Copy/Paste)

```powershell
cd "C:\Users\Ayush Manoj Garg\OneDrive\Desktop\mpstme\here tech hackathon\HERE_Mark42"
docker-compose up -d
python scripts/setup_db.py

cd osm-verifier
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000

# In another terminal:
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/health | Select-Object -ExpandProperty Content
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/data-sources | Select-Object -ExpandProperty Content

$body = @{name='Burger & Lobster'; address='Singapore'} | ConvertTo-Json
Invoke-WebRequest -UseBasicParsing -Method Post -Uri http://127.0.0.1:8000/verify -ContentType 'application/json' -Body $body | Select-Object -ExpandProperty Content
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/contradictions | Select-Object -ExpandProperty Content

.\venv\Scripts\python.exe scripts\evaluate_model.py
```
