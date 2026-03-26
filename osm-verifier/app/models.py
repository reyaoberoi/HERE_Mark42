from pydantic import BaseModel
from typing import Optional, List

class VerifyRequest(BaseModel):
    osm_node_id: str
    lat: float
    lon: float
    name: str
    tags: dict = {}

class SourceResult(BaseModel):
    source: str
    signal: str          # "active" | "closed" | "unknown"
    confidence: float    # 0.0 – 1.0
    detail: Optional[str] = None

class VerifyResponse(BaseModel):
    osm_node_id: str
    confidence: float
    recommendation: str          # "ACCEPT" | "REVIEW" | "REJECT"
    sources: List[SourceResult]
    narrative: str
    before_image_url: Optional[str] = None
    after_image_url: Optional[str] = None
    changeset_diff: Optional[dict] = None