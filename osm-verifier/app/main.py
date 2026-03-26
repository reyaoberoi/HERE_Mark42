from fastapi import FastAPI
from app.models import VerifyRequest, VerifyResponse
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="OSM Verifier API")

@app.get("/")
def root():
    return {"status": "ok"}

@app.post("/verify", response_model=VerifyResponse)
async def verify(req: VerifyRequest):
    # Stub — replace with real pipeline after S2/S3/S6/S7
    return VerifyResponse(
        osm_node_id=req.osm_node_id,
        confidence=0.5,
        recommendation="REVIEW",
        sources=[],
        narrative="Stub response — pipeline not yet wired.",
        before_image_url=None,
        after_image_url=None,
        changeset_diff=None,
    )