import os
import psycopg2
import subprocess
import time
import zipfile
import httpx
from dotenv import load_dotenv

# Load env from osm-verifier/.env if it exists
load_dotenv("osm-verifier/.env")

DB_NAME = os.getenv("POSTGRES_DB", "osm_sg")
DB_USER = os.getenv("POSTGRES_USER", "user")
DB_PASS = os.getenv("POSTGRES_PASSWORD", "password")
DB_HOST = "localhost" # Since we are running from host connecting to mapped port
DB_PORT = os.getenv("POSTGRES_PORT", "5432")

# Internal container details for ogr2ogr
DB_CONTAINER_HOST = "db" 
NETWORK_NAME = "here_mark42_default"
GPKG_URL = "https://download.geofabrik.de/asia/malaysia-singapore-brunei-latest-free.gpkg.zip"
GPKG_FILENAME = "malaysia-singapore-brunei.gpkg"
DATA_DIR_PATH = os.path.abspath("Data")

def download_data():
    zip_path = os.path.join(DATA_DIR_PATH, "data.zip")
    if not os.path.exists(DATA_DIR_PATH):
        os.makedirs(DATA_DIR_PATH)
    
    print(f"Downloading data from {GPKG_URL}...")
    with httpx.stream("GET", GPKG_URL, follow_redirects=True) as r:
        r.raise_for_status()
        with open(zip_path, "wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    print("Download complete.")
    return zip_path

def extract_data(zip_path):
    print(f"Extracting {zip_path}...")
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(DATA_DIR_PATH)
        # Find the .gpkg file and rename it to GPKG_FILENAME
        for file in os.listdir(DATA_DIR_PATH):
            if file.endswith(".gpkg"):
                old_path = os.path.join(DATA_DIR_PATH, file)
                new_path = os.path.join(DATA_DIR_PATH, GPKG_FILENAME)
                if os.path.exists(new_path):
                    os.remove(new_path)
                os.rename(old_path, new_path)
                break
    os.remove(zip_path)
    print(f"Extraction complete. Data saved as {GPKG_FILENAME}")

def wait_for_db(conn_str, timeout=30):
    start_time = time.time()
    while True:
        try:
            conn = psycopg2.connect(conn_str)
            conn.close()
            print("Database is ready!")
            return True
        except psycopg2.OperationalError:
            if time.time() - start_time > timeout:
                print("Timeout waiting for database.")
                return False
            print("Waiting for database...")
            time.sleep(2)

def setup_extensions(conn):
    with conn.cursor() as cur:
        print("Enabling extensions...")
        cur.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
        cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
    conn.commit()

def load_data_via_docker():
    print(f"Loading Singapore POIs from {GPKG_FILENAME} via Docker (ghcr.io/osgeo/gdal)...")
    
    # Singapore BBox: 103.6 1.1 104.1 1.5
    image = "ghcr.io/osgeo/gdal:ubuntu-small-latest"
    
    # We load gis_osm_pois_free layer which contains point POIs
    # and gis_osm_pois_a_free which contains area POIs (like malls/large shops)
    
    def run_ogr(layer, append=False, area_to_centroid=False):
        mode = "-append" if append else "-overwrite"
        cmd = [
            "docker", "run", "--rm",
            "--network", NETWORK_NAME,
            "-v", f"{DATA_DIR_PATH}:/data",
            image,
            "ogr2ogr",
            "-f", "PostgreSQL",
            f"PG:dbname={DB_NAME} user={DB_USER} password={DB_PASS} host={DB_CONTAINER_HOST} port=5432",
            f"/data/{GPKG_FILENAME}",
            layer,
            "-nln", "raw_osm_data",
            "-spat", "103.6", "1.1", "104.1", "1.5",
            mode,
            "-progress"
        ]

        if area_to_centroid:
            # The area layer has polygon geometries; convert to point centroids so it can be
            # appended into the same Point table created from gis_osm_pois_free.
            cmd.extend([
                "-dialect", "SQLite",
                "-sql",
                "SELECT ST_Centroid(geom) AS geom, osm_id, code, fclass, name "
                "FROM gis_osm_pois_a_free"
            ])

        print(f"Running: {' '.join(cmd)}")
        subprocess.run(cmd, check=True)

    try:
        # Pass 1: Points
        print(">>> Pass 1: Loading point POIs...")
        run_ogr("gis_osm_pois_free", append=False)
        
        # Pass 2: Polygons (Append to same table)
        print(">>> Pass 2: Appending area POIs...")
        run_ogr("gis_osm_pois_a_free", append=True, area_to_centroid=True)
        
        print("Data loaded successfully with Singapore filter (Points + Areas).")
        return True
    except Exception as e:
        print(f"Error loading data: {e}")
        return False

def create_indexes(conn):
    with conn.cursor() as cur:
        print("Creating spatial indexes...")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_raw_osm_data_geom ON raw_osm_data USING GIST (geom);")
    conn.commit()

if __name__ == "__main__":
    # Check if data exists, if not download and extract
    gpkg_path = os.path.join(DATA_DIR_PATH, GPKG_FILENAME)
    if not os.path.exists(gpkg_path):
        print(f"Data file {GPKG_FILENAME} not found.")
        zip_path = download_data()
        extract_data(zip_path)
    
    conn_str = f"dbname={DB_NAME} user={DB_USER} password={DB_PASS} host={DB_HOST} port={DB_PORT}"
    
    if wait_for_db(conn_str):
        conn = psycopg2.connect(conn_str)
        try:
            setup_extensions(conn)
            if load_data_via_docker():
                create_indexes(conn)
                print("Database setup complete.")
            else:
                print("Database loading failed. Skipping index creation.")
        finally:
            conn.close()
