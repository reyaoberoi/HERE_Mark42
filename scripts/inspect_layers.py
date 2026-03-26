import sqlite3
import os

gpkg_path = r"c:\Projects\HERE_Hackathon\HERE_Mark42\Data\malaysia-singapore-brunei.gpkg"
if os.path.exists(gpkg_path):
    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()
    cur.execute("SELECT table_name FROM gpkg_contents")
    tables = cur.fetchall()
    print("Layer Names:")
    for t in tables:
        print(t[0])
    conn.close()
else:
    print("File not found.")
