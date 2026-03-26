"""
End-to-end pipeline test — search for a real Singapore POI and verify it.
Run with: python test_pipeline.py
"""
import httpx
import json
import sys

BASE = "http://127.0.0.1:8000"

def main():
    query = sys.argv[1] if len(sys.argv) > 1 else "BreadTalk"
    print(f"\n{'='*60}")
    print(f"  Pipeline Test — searching for: {query}")
    print(f"{'='*60}\n")

    # Step 1: Search
    print("[1/3] Searching...")
    r = httpx.get(f"{BASE}/search", params={"q": query}, timeout=15)
    search = r.json()
    
    if not search.get("candidates"):
        print(f"  ❌ No candidates found for '{query}'")
        return
    
    print(f"  ✅ Found {search['count']} candidates:")
    for i, c in enumerate(search["candidates"][:5]):
        print(f"     {i+1}. {c['name']} (id={c['osm_node_id']}, lat={c['lat']:.5f}, lon={c['lon']:.5f})")
    
    # Step 2: Pick first candidate and verify
    candidate = search["candidates"][0]
    print(f"\n[2/3] Verifying: {candidate['name']} (node {candidate['osm_node_id']})")
    print(f"      Coordinates: {candidate['lat']:.6f}, {candidate['lon']:.6f}")
    print("      Waiting for signals...")
    
    verify_payload = {
        "osm_node_id": candidate["osm_node_id"],
        "lat": candidate["lat"],
        "lon": candidate["lon"],
        "name": candidate["name"],
        "tags": candidate.get("tags", {})
    }
    
    r = httpx.post(f"{BASE}/verify", json=verify_payload, timeout=60)
    result = r.json()
    
    # Step 3: Show results
    print(f"\n[3/3] Results:")
    print(f"  Confidence: {result['confidence']:.1f}%")
    print(f"  Recommendation: {result['recommendation']}")
    print(f"  Narrative: {result['narrative']}")
    print(f"\n  Signal Breakdown:")
    for src in result["sources"]:
        emoji = {"active": "🟢", "closed": "🔴", "unknown": "⬜"}.get(src["signal"], "❓")
        print(f"    {emoji} [{src['source'].upper()}] {src['signal']} (conf: {src['confidence']:.2f}) — {src.get('detail', 'N/A')}")
    
    print(f"\n{'='*60}")
    print(f"  Full JSON response saved to test_result.json")
    print(f"{'='*60}\n")
    
    with open("test_result.json", "w") as f:
        json.dump(result, indent=2, fp=f)

if __name__ == "__main__":
    main()
