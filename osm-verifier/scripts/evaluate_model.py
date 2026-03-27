import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main import verify
from models import VerifyRequest

SAMPLES_PATH = ROOT / "scripts" / "evaluation_samples.json"
OUT_DIR = ROOT / "evaluation"


def _status_match(pred: str, exp: str) -> bool:
    if not exp:
        return True
    p = (pred or "").strip().lower()
    e = (exp or "").strip().lower()
    if p == e:
        return True
    # Allow "Uncertain" predictions to map to review-adjacent outcomes.
    if p == "uncertain" and e in {"established", "recently closed", "new place"}:
        return False
    return False


async def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with SAMPLES_PATH.open("r", encoding="utf-8") as f:
        samples = json.load(f)

    rows = []
    changesets = []

    for i, sample in enumerate(samples, start=1):
        req = VerifyRequest(name=sample["name"], address=sample["address"])
        result = await verify(req)
        data = result.model_dump()

        status_ok = _status_match(data.get("predicted_status"), sample.get("expected_status"))
        rec_ok = (data.get("recommendation") == sample.get("expected_recommendation"))
        both_ok = status_ok and rec_ok

        row = {
            "index": i,
            "name": sample["name"],
            "address": sample["address"],
            "expected_status": sample.get("expected_status"),
            "predicted_status": data.get("predicted_status"),
            "expected_recommendation": sample.get("expected_recommendation"),
            "predicted_recommendation": data.get("recommendation"),
            "confidence": data.get("confidence"),
            "source_count": data.get("source_count", 0),
            "considered_sources": data.get("considered_sources", []),
            "status_match": status_ok,
            "recommendation_match": rec_ok,
            "overall_match": both_ok,
        }
        rows.append(row)

        if data.get("changeset_diff"):
            changesets.append(
                {
                    "name": sample["name"],
                    "osm_id": data.get("osm_id"),
                    "predicted_status": data.get("predicted_status"),
                    "recommendation": data.get("recommendation"),
                    "changeset_diff": data.get("changeset_diff"),
                }
            )

    n = len(rows)
    status_acc = sum(1 for r in rows if r["status_match"]) / n if n else 0.0
    rec_acc = sum(1 for r in rows if r["recommendation_match"]) / n if n else 0.0
    overall_acc = sum(1 for r in rows if r["overall_match"]) / n if n else 0.0

    report = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "sample_count": n,
        "status_accuracy": round(status_acc, 4),
        "recommendation_accuracy": round(rec_acc, 4),
        "overall_accuracy": round(overall_acc, 4),
        "rows": rows,
    }

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = OUT_DIR / f"model_eval_{ts}.json"
    latest_path = OUT_DIR / "model_eval_latest.json"
    changeset_path = OUT_DIR / "changeset_diffs_latest.jsonl"

    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    latest_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    with changeset_path.open("w", encoding="utf-8") as f:
        for item in changesets:
            f.write(json.dumps(item) + "\n")

    print(f"Saved report: {report_path}")
    print(f"Saved latest report: {latest_path}")
    print(f"Saved changeset dataset: {changeset_path}")
    print(f"Overall accuracy: {report['overall_accuracy']:.2%}")


if __name__ == "__main__":
    asyncio.run(main())
