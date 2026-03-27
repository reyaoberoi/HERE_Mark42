# app/osm/changeset.py
import os
import httpx
import xml.etree.ElementTree as ET

OSM_API  = "https://api.openstreetmap.org/api/0.6"
OSM_USER = os.getenv("OSM_USERNAME", "")
OSM_PASS = os.getenv("OSM_PASSWORD", "")


async def submit_osm_changeset(osm_id: str, tags_after: dict) -> str:
    """
    Submit a disused: changeset to OSM using basic auth.
    For production: replace with OAuth 2.0 PKCE flow.
    Returns the live changeset URL.
    """
    if not OSM_USER or not OSM_PASS:
        raise ValueError("OSM_USERNAME and OSM_PASSWORD must be set in .env")

    auth = (OSM_USER, OSM_PASS)

    async with httpx.AsyncClient(timeout=15, auth=auth) as client:
        # 1. Create changeset
        cs_xml = """<osm><changeset>
          <tag k="comment" v="Automated POI closure audit by osm-sg-validator"/>
          <tag k="source" v="osm-sg-validator"/>
          <tag k="created_by" v="osm-sg-validator/1.0"/>
        </changeset></osm>"""
        r1 = await client.put(
            f"{OSM_API}/changeset/create",
            content=cs_xml,
            headers={"Content-Type": "text/xml"}
        )
        r1.raise_for_status()
        changeset_id = r1.text.strip()

        # 2. Fetch current node
        r2 = await client.get(f"{OSM_API}/node/{osm_id}")
        r2.raise_for_status()
        root = ET.fromstring(r2.text)
        node = root.find("node")
        version  = node.get("version")
        node_lat = node.get("lat")
        node_lon = node.get("lon")

        # 3. Build updated node XML
        # Escape XML special characters in tag values
        def _escape(v: str) -> str:
            return str(v).replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")

        tag_xml = "\n".join(
            f'<tag k="{_escape(k)}" v="{_escape(v)}"/>' for k, v in tags_after.items()
        )
        node_xml = f"""<osm><node id="{osm_id}" version="{version}"
          changeset="{changeset_id}" lat="{node_lat}" lon="{node_lon}">
          {tag_xml}
        </node></osm>"""

        r3 = await client.put(
            f"{OSM_API}/node/{osm_id}",
            content=node_xml,
            headers={"Content-Type": "text/xml"}
        )
        r3.raise_for_status()

        # 4. Close changeset
        await client.put(f"{OSM_API}/changeset/{changeset_id}/close")

    return f"https://www.openstreetmap.org/changeset/{changeset_id}"
