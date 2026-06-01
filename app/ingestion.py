import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Event
from app.schemas import EventCreate

logger = logging.getLogger(__name__)

MAX_BATCH_SIZE = 500

router = APIRouter(prefix="/events", tags=["Events"])

INGEST_ENDPOINT = "/events/ingest"

INGEST_REQUEST_OPENAPI: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "description": "Array of EventCreate objects. Each item is validated independently.",
        "content": {
            "application/json": {
                "schema": {
                    "type": "array",
                    "maxItems": MAX_BATCH_SIZE,
                    "items": {
                        "type": "object",
                        "required": [
                            "event_id",
                            "store_id",
                            "camera_id",
                            "visitor_id",
                            "event_type",
                            "timestamp",
                            "confidence",
                        ],
                        "properties": {
                            "event_id": {"type": "string"},
                            "store_id": {"type": "string"},
                            "camera_id": {"type": "string"},
                            "visitor_id": {"type": "string"},
                            "event_type": {
                                "type": "string",
                                "enum": [
                                    "ENTRY",
                                    "EXIT",
                                    "ZONE_ENTER",
                                    "ZONE_EXIT",
                                    "ZONE_DWELL",
                                    "BILLING_QUEUE_JOIN",
                                    "BILLING_QUEUE_ABANDON",
                                    "REENTRY",
                                ],
                            },
                            "timestamp": {"type": "string", "format": "date-time"},
                            "zone_id": {"type": "string", "nullable": True},
                            "dwell_ms": {
                                "type": "integer",
                                "minimum": 0,
                                "default": 0,
                            },
                            "is_staff": {"type": "boolean", "default": False},
                            "confidence": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 1,
                            },
                            "metadata": {
                                "type": "object",
                                "properties": {
                                    "queue_depth": {
                                        "type": "integer",
                                        "nullable": True,
                                    },
                                    "sku_zone": {"type": "string", "nullable": True},
                                    "session_seq": {
                                        "type": "integer",
                                        "nullable": True,
                                    },
                                },
                            },
                        },
                    },
                },
                "example": [
                    {
                        "event_id": "evt_001",
                        "store_id": "STORE_001",
                        "camera_id": "CAM_ENTRY_01",
                        "visitor_id": "VIS_001",
                        "event_type": "ENTRY",
                        "timestamp": "2026-03-03T14:22:10Z",
                        "zone_id": None,
                        "dwell_ms": 0,
                        "is_staff": False,
                        "confidence": 0.95,
                        "metadata": {"session_seq": 1},
                    }
                ],
            }
        },
    }
}


class FailedEvent(BaseModel):
    """Details for an event that could not be ingested."""

    event_id: str
    reason: str


class BatchIngestResponse(BaseModel):
    """Response returned after processing a batch ingestion request."""

    success: bool
    inserted_count: int
    duplicate_count: int
    failed_count: int
    failed_events: list[FailedEvent]


class ErrorResponse(BaseModel):
    """Error response for request-level failures."""

    success: bool
    message: str


@dataclass
class BatchSaveResult:
    """Database save result with inserted, duplicate, and failed event counts."""

    inserted_count: int = 0
    duplicate_count: int = 0
    failed_events: list[FailedEvent] = field(default_factory=list)


def log_ingestion_result(
    *,
    request_id: str,
    batch_size: int,
    inserted_count: int,
    duplicate_count: int,
    failed_count: int,
    processing_time_ms: int,
    status_code: int,
) -> None:
    """Emit a structured JSON log entry for every ingestion request outcome."""
    logger.info(
        json.dumps(
            {
                "request_id": request_id,
                "endpoint": INGEST_ENDPOINT,
                "batch_size": batch_size,
                "inserted_count": inserted_count,
                "duplicate_count": duplicate_count,
                "failed_count": failed_count,
                "processing_time_ms": processing_time_ms,
                "status_code": status_code,
            }
        )
    )


class EventIngestionService:
    """Coordinates validation, deduplication, persistence, and reporting for events."""

    def __init__(self, db: Session) -> None:
        """Initialize the service with a request-scoped database session."""
        self.db = db

    def validate_event(self, raw_event: Any) -> tuple[EventCreate | None, FailedEvent | None]:
        """Validate an incoming payload item and return either an event or failure detail."""
        event_id = self._extract_event_id(raw_event)

        if not isinstance(raw_event, dict):
            return None, FailedEvent(event_id=event_id, reason="missing field")

        try:
            event = EventCreate.model_validate(raw_event)
        except ValidationError as exc:
            return None, FailedEvent(
                event_id=event_id,
                reason=self._validation_reason(exc),
            )

        if self._has_missing_required_string(event):
            return None, FailedEvent(event_id=event.event_id, reason="missing field")

        return event, None

    def is_duplicate(self, event_id: str) -> bool:
        """Return true when an event ID already exists in persistent storage."""
        statement = select(Event.event_id).where(Event.event_id == event_id).limit(1)
        return self.db.execute(statement).scalar_one_or_none() is not None

    def build_event_model(self, event: EventCreate) -> Event:
        """Convert a validated Pydantic event into a SQLAlchemy ORM instance."""
        return Event(
            event_id=event.event_id,
            store_id=event.store_id,
            camera_id=event.camera_id,
            visitor_id=event.visitor_id,
            event_type=event.event_type.value,
            timestamp=event.timestamp,
            zone_id=event.zone_id,
            dwell_ms=event.dwell_ms,
            is_staff=event.is_staff,
            confidence=event.confidence,
            metadata_json=event.metadata.model_dump_json(),
        )

    def save_batch(self, events: list[Event]) -> BatchSaveResult:
        """Persist events while classifying unique constraint races as duplicates."""
        result = BatchSaveResult()
        inserted_event_ids: list[str] = []

        for event in events:
            try:
                with self.db.begin_nested():
                    self.db.add(event)
                    self.db.flush()
                result.inserted_count += 1
                inserted_event_ids.append(event.event_id)
            except IntegrityError:
                if self.is_duplicate(event.event_id):
                    result.duplicate_count += 1
                    logger.info(
                        "event_duplicate_on_insert",
                        extra={"event_id": event.event_id},
                    )
                else:
                    result.failed_events.append(
                        FailedEvent(event_id=event.event_id, reason="database error")
                    )
                    logger.exception(
                        "event_integrity_error",
                        extra={"event_id": event.event_id},
                    )
            except SQLAlchemyError:
                result.failed_events.append(
                    FailedEvent(event_id=event.event_id, reason="database error")
                )
                logger.exception(
                    "event_insert_failed",
                    extra={"event_id": event.event_id},
                )

        try:
            self.db.commit()
        except SQLAlchemyError:
            self.db.rollback()
            logger.exception("batch_commit_failed")
            return BatchSaveResult(
                inserted_count=0,
                duplicate_count=result.duplicate_count,
                failed_events=[
                    FailedEvent(event_id=event_id, reason="database error")
                    for event_id in inserted_event_ids
                ],
            )

        return result

    def ingest_batch(self, payload: list[Any]) -> BatchIngestResponse:
        """Validate, deduplicate, save, and summarize an event batch."""
        inserted_candidates: list[Event] = []
        failed_events: list[FailedEvent] = []
        duplicate_count = 0
        seen_event_ids: set[str] = set()

        for raw_event in payload:
            event, failure = self.validate_event(raw_event)
            if failure is not None:
                failed_events.append(failure)
                continue

            if event is None:
                failed_events.append(FailedEvent(event_id="", reason="missing field"))
                continue

            if event.event_id in seen_event_ids or self.is_duplicate(event.event_id):
                duplicate_count += 1
                continue

            seen_event_ids.add(event.event_id)
            inserted_candidates.append(self.build_event_model(event))

        save_result = self.save_batch(inserted_candidates)
        failed_events.extend(save_result.failed_events)
        duplicate_count += save_result.duplicate_count

        return BatchIngestResponse(
            success=True,
            inserted_count=save_result.inserted_count,
            duplicate_count=duplicate_count,
            failed_count=len(failed_events),
            failed_events=failed_events,
        )

    @staticmethod
    def _extract_event_id(raw_event: Any) -> str:
        """Best-effort extraction of an event ID for failure reporting."""
        if isinstance(raw_event, dict):
            value = raw_event.get("event_id")
            return str(value) if value is not None else ""
        return ""

    @staticmethod
    def _validation_reason(exc: ValidationError) -> str:
        """Map Pydantic validation errors to public ingestion failure reasons."""
        errors = exc.errors()
        if not errors:
            return "missing field"

        first_error = errors[0]
        field = first_error.get("loc", ("",))[0]
        error_type = str(first_error.get("type", ""))

        if error_type == "missing":
            return "missing field"
        if field == "confidence":
            return "invalid confidence"
        if field == "dwell_ms":
            return "invalid dwell_ms"
        if field == "timestamp":
            return "invalid timestamp"
        if field == "event_type":
            return "invalid event type"

        return "missing field"

    @staticmethod
    def _has_missing_required_string(event: EventCreate) -> bool:
        """Return true when a required string field is blank after validation."""
        required_values = (
            event.event_id,
            event.store_id,
            event.camera_id,
            event.visitor_id,
        )
        return any(value.strip() == "" for value in required_values)


@router.post(
    "/ingest",
    response_model=BatchIngestResponse,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
    },
    openapi_extra=INGEST_REQUEST_OPENAPI,
)
def ingest_events(
    payload: Annotated[Any, Body(...)],
    db: Annotated[Session, Depends(get_db)],
) -> BatchIngestResponse | JSONResponse:
    """Ingest a batch of CCTV analytics events with idempotent deduplication."""
    request_id = str(uuid.uuid4())
    started_at = time.perf_counter()

    if not isinstance(payload, list):
        processing_time_ms = int((time.perf_counter() - started_at) * 1000)
        log_ingestion_result(
            request_id=request_id,
            batch_size=0,
            inserted_count=0,
            duplicate_count=0,
            failed_count=1,
            processing_time_ms=processing_time_ms,
            status_code=status.HTTP_400_BAD_REQUEST,
        )
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"success": False, "message": "Request body must be a list"},
        )

    if len(payload) > MAX_BATCH_SIZE:
        processing_time_ms = int((time.perf_counter() - started_at) * 1000)
        log_ingestion_result(
            request_id=request_id,
            batch_size=len(payload),
            inserted_count=0,
            duplicate_count=0,
            failed_count=0,
            processing_time_ms=processing_time_ms,
            status_code=status.HTTP_400_BAD_REQUEST,
        )
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"success": False, "message": "Maximum batch size exceeded"},
        )

    try:
        service = EventIngestionService(db)
        response = service.ingest_batch(payload)
    except Exception:
        logger.exception(
            "event_ingestion_unexpected_error",
            extra={"request_id": request_id, "endpoint": INGEST_ENDPOINT},
        )
        processing_time_ms = int((time.perf_counter() - started_at) * 1000)
        log_ingestion_result(
            request_id=request_id,
            batch_size=len(payload),
            inserted_count=0,
            duplicate_count=0,
            failed_count=len(payload),
            processing_time_ms=processing_time_ms,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "message": "Internal server error"},
        )

    processing_time_ms = int((time.perf_counter() - started_at) * 1000)
    log_ingestion_result(
        request_id=request_id,
        batch_size=len(payload),
        inserted_count=response.inserted_count,
        duplicate_count=response.duplicate_count,
        failed_count=response.failed_count,
        processing_time_ms=processing_time_ms,
        status_code=status.HTTP_200_OK,
    )

    return response
