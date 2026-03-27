# app/sources/mapillary.py
import os
import io
import asyncio
import httpx
from datetime import datetime, timezone

MAPILLARY_API = "https://graph.mapillary.com/images"


def _format_capture_date(raw_value) -> str | None:
    if raw_value is None:
        return None
    if isinstance(raw_value, (int, float)):
        try:
            # Mapillary may return unix milliseconds.
            ts = float(raw_value)
            if ts > 1e12:
                ts = ts / 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        except Exception:
            return str(raw_value)
    text = str(raw_value)
    # Handles ISO strings like 2023-07-11T10:20:30Z
    return text[:10] if len(text) >= 10 else text


def _mapillary_token() -> str:
    return os.getenv("MAPILLARY_ACCESS_TOKEN", "") or os.getenv("MAPILLARY_TOKEN", "")


def _mapillary_headers(token: str) -> dict:
    if not token:
        return {}
    return {
        "Authorization": f"OAuth {token}",
        "User-Agent": "osm-sg-validator/1.0",
    }


async def fetch_mapillary(lat: float, lon: float) -> dict:
    """
    Fetch oldest and newest Mapillary images within 50m of coordinates.
    Compute SSIM structural similarity diff between them.
    If no Mapillary token, returns UNKNOWN gracefully.
    """
    token = _mapillary_token()
    if not token:
        return {
            "source": "mapillary",
            "status": "UNKNOWN",
            "confidence": 0.0,
            "detail": "MAPILLARY_ACCESS_TOKEN not set",
            "before_image_url": None, "after_image_url": None,
            "before_date": None, "after_date": None,
            "visual_delta_score": None, "change_class": None,
        }

    try:
        async with httpx.AsyncClient(timeout=12) as client:
            params = {
                "fields": "id,captured_at,thumb_2048_url,thumb_1024_url,thumb_256_url",
                "bbox": f"{lon-0.0005},{lat-0.0005},{lon+0.0005},{lat+0.0005}",
                "limit": 50,
            }
            resp = await client.get(MAPILLARY_API, params=params, headers=_mapillary_headers(token))
            if resp.status_code in (401, 403):
                # Some tokens require Bearer instead of OAuth.
                resp = await client.get(
                    MAPILLARY_API,
                    params=params,
                    headers={"Authorization": f"Bearer {token}", "User-Agent": "osm-sg-validator/1.0"},
                )
            resp.raise_for_status()
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

        old_date = _format_capture_date(oldest.get("captured_at"))
        new_date = _format_capture_date(newest.get("captured_at"))

        before_url = oldest.get("thumb_1024_url") or oldest.get("thumb_256_url") or oldest.get("thumb_2048_url")
        after_url  = newest.get("thumb_1024_url") or newest.get("thumb_256_url") or newest.get("thumb_2048_url")

        if not before_url or not after_url:
            return {
                "source": "mapillary", "status": "UNKNOWN", "confidence": 0.0,
                "detail": "Mapillary images found but thumbnail URLs unavailable",
                "before_image_url": None, "after_image_url": None,
                "before_date": old_date, "after_date": new_date,
                "visual_delta_score": None, "change_class": None,
            }

        # Download and compare
        delta_score, change_class = await _compute_ssim_diff(before_url, after_url)

        # Convert change_class to ACTIVE/CLOSED signal
        if change_class == "major_change":
            status = "CLOSED"
            confidence = 0.65
            detail = f"Major visual change detected between {old_date or 'unknown'} and {new_date or 'unknown'}"
        elif change_class == "no_change":
            status = "ACTIVE"
            confidence = 0.60
            detail = f"Shopfront unchanged between {old_date or 'unknown'} and {new_date or 'unknown'}"
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
            "before_date": old_date,
            "after_date":  new_date,
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
