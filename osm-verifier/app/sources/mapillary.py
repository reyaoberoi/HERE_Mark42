# app/sources/mapillary.py
import os
import io
import asyncio
import httpx

MAPILLARY_API = "https://graph.mapillary.com/images"
TOKEN = os.getenv("MAPILLARY_TOKEN", "")
HEADERS = {"Authorization": f"OAuth {TOKEN}"} if TOKEN else {}


async def fetch_mapillary(lat: float, lon: float) -> dict:
    """
    Fetch oldest and newest Mapillary images within 50m of coordinates.
    Compute SSIM structural similarity diff between them.
    If no Mapillary token, returns UNKNOWN gracefully.
    """
    if not TOKEN:
        return {
            "source": "mapillary",
            "status": "UNKNOWN",
            "confidence": 0.0,
            "detail": "MAPILLARY_TOKEN not set",
            "before_image_url": None, "after_image_url": None,
            "before_date": None, "after_date": None,
            "visual_delta_score": None, "change_class": None,
        }

    try:
        async with httpx.AsyncClient(timeout=12) as client:
            resp = await client.get(MAPILLARY_API, params={
                "fields": "id,captured_at,thumb_256_url",
                "bbox": f"{lon-0.0005},{lat-0.0005},{lon+0.0005},{lat+0.0005}",
                "limit": 50,
            }, headers=HEADERS)
            data = resp.json()
            images = data.get("data", [])

        if len(images) < 2:
            return {
                "source": "mapillary", "status": "UNKNOWN", "confidence": 0.0,
                "detail": f"Only {len(images)} Mapillary image(s) found near location",
                "before_image_url": None, "after_image_url": None,
                "before_date": None, "after_date": None,
                "visual_delta_score": None, "change_class": None,
            }

        # Sort by date
        images.sort(key=lambda x: x.get("captured_at", ""))
        oldest = images[0]
        newest = images[-1]

        before_url = oldest.get("thumb_256_url")
        after_url  = newest.get("thumb_256_url")

        # Download and compare
        delta_score, change_class = await _compute_ssim_diff(before_url, after_url)

        # Convert change_class to ACTIVE/CLOSED signal
        if change_class == "major_change":
            status = "CLOSED"
            confidence = 0.65
            detail = f"Major visual change detected between {oldest['captured_at'][:10]} and {newest['captured_at'][:10]}"
        elif change_class == "no_change":
            status = "ACTIVE"
            confidence = 0.60
            detail = f"Shopfront unchanged between {oldest['captured_at'][:10]} and {newest['captured_at'][:10]}"
        else:
            status = "UNKNOWN"
            confidence = 0.30
            detail = "Minor visual change — inconclusive"

        return {
            "source": "mapillary",
            "status": status,
            "confidence": confidence,
            "detail": detail,
            "before_image_url": before_url,
            "after_image_url":  after_url,
            "before_date": oldest.get("captured_at", "")[:10],
            "after_date":  newest.get("captured_at", "")[:10],
            "visual_delta_score": delta_score,
            "change_class": change_class,
        }

    except Exception as e:
        return {
            "source": "mapillary", "status": "UNKNOWN", "confidence": 0.0,
            "detail": str(e),
            "before_image_url": None, "after_image_url": None,
            "before_date": None, "after_date": None,
            "visual_delta_score": None, "change_class": None,
        }


async def _compute_ssim_diff(url1: str, url2: str):
    """Download two images and compute SSIM structural similarity."""
    try:
        import numpy as np
        from PIL import Image
        from skimage.metrics import structural_similarity as ssim

        async with httpx.AsyncClient(timeout=10) as client:
            r1, r2 = await asyncio.gather(client.get(url1), client.get(url2))

        img1 = Image.open(io.BytesIO(r1.content)).convert("L").resize((128, 128))
        img2 = Image.open(io.BytesIO(r2.content)).convert("L").resize((128, 128))

        arr1 = np.array(img1, dtype=np.float32) / 255.0
        arr2 = np.array(img2, dtype=np.float32) / 255.0

        score = ssim(arr1, arr2, data_range=1.0)
        bright_delta = abs(arr1.mean() - arr2.mean())
        visual_delta = (1.0 - score) * 0.7 + bright_delta * 0.3

        if score < 0.55:
            change_class = "major_change"
        elif score < 0.80:
            change_class = "minor_change"
        else:
            change_class = "no_change"

        return round(visual_delta, 3), change_class

    except Exception:
        return None, "unknown"
