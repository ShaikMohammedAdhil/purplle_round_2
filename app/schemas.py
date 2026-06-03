from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class EventType(str, Enum):
    """Supported event types emitted by the CCTV analytics pipeline."""

    ENTRY = "ENTRY"
    EXIT = "EXIT"
    ZONE_ENTER = "ZONE_ENTER"
    ZONE_EXIT = "ZONE_EXIT"
    ZONE_DWELL = "ZONE_DWELL"
    BILLING_QUEUE_JOIN = "BILLING_QUEUE_JOIN"
    BILLING_QUEUE_ABANDON = "BILLING_QUEUE_ABANDON"
    REENTRY = "REENTRY"


class EventMetadata(BaseModel):
    """Optional structured metadata attached to an ingested event."""

    queue_depth: int | None = None
    sku_zone: str | None = None
    session_seq: int | None = None
    track_id: int | None = None
    frame: int | None = None


class EventCreate(BaseModel):
    """Request payload for creating an analytics event."""

    event_id: str
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: EventType
    timestamp: datetime
    zone_id: str | None = None
    dwell_ms: int = Field(default=0, ge=0)
    is_staff: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: EventMetadata = Field(default_factory=EventMetadata)


class EventResponse(BaseModel):
    """Response returned after attempting to ingest one event."""

    success: bool
    message: str
    event_id: str

    model_config = ConfigDict(from_attributes=True)


class IngestResponse(BaseModel):
    """Aggregate response for batch event ingestion."""

    inserted_count: int
    duplicate_count: int
    failed_count: int
