# 🗄️ Database Initialization Guide

Follow these steps to set up the PostGIS database and load the Singapore OSM data on your local machine.

## Prerequisites
- **Docker Desktop** installed and running.
- **Python 3.10+** with a virtual environment (venv) activated.
- **GDAL Data**: Ensure [Data/malaysia-singapore-brunei.gpkg](file:///c:/Projects/HERE_Hackathon/HERE_Mark42/Data/malaysia-singapore-brunei.gpkg) exists in the project root.

---

## 🚀 Setup Steps

### 1. Configure Environment
Copy the example environment file and update if necessary:
```powershell
cp osm-verifier/.env.example osm-verifier/.env
```
> [!NOTE]
> The default values in [.env.example](file:///c:/Projects/HERE_Hackathon/HERE_Mark42/osm-verifier/.env.example) are pre-tuned for the local Docker setup.

### 2. Start PostgreSQL/PostGIS
Launch the database container in the background:
```powershell
docker-compose up -d
```
Verify the container is running:
```powershell
docker ps
```

### 3. Install Python Dependencies
Ensure you have `psycopg2` installed (used by the setup script):
```powershell
pip install -r osm-verifier/requirements.txt
```

### 4. Initialize Schema & Load Data
Run the automation script from the **project root**:
```powershell
python scripts/setup_db.py
```
This script will:
- Wait for the database to be ready.
- Enable `postgis` and `pg_trgm` extensions.
- Pull the GDAL Docker image to import the [.gpkg](file:///c:/Projects/HERE_Hackathon/HERE_Mark42/Data/malaysia-singapore-brunei.gpkg) data.
- Create spatial indexes for performance.

---

## 🔍 Verification
To verify the database is populated, you can run:
```powershell
# From the root directory
cd osm-verifier
python -m uvicorn main:app
```
Then visit `http://127.0.0.1:8000/` in your browser. You should see `database: Connected` and a `row_count` of around 750+.

## 🛠️ Troubleshooting
- **Docker Network Error**: If the script fails to find the network, ensure `docker-compose up` was run first.
- **Port Conflict**: If port `5432` is already in use, update [docker-compose.yaml](file:///c:/Projects/HERE_Hackathon/HERE_Mark42/docker-compose.yaml) and [.env](file:///c:/Projects/HERE_Hackathon/HERE_Mark42/osm-verifier/.env).
