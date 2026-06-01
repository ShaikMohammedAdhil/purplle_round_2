import json
import logging
import time
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.heatmap_service import HeatmapService, ZoneHeatmapItem

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Heatmap"])


def log_heatmap_result(
    *,
    request_id: str,
    store_id: str,
    start_time: datetime | None,
    end_time: datetime | None,
    processing_time_ms: int,
    status_code: int,
) -> None:
    """Emit a structured JSON log entry for heatmap request outcomes."""
    logger.info(
        json.dumps(
            {
                "request_id": request_id,
                "endpoint": "/stores/{store_id}/heatmap",
                "store_id": store_id,
                "start_time": start_time.isoformat() if start_time else None,
                "end_time": end_time.isoformat() if end_time else None,
                "processing_time_ms": processing_time_ms,
                "status_code": status_code,
            }
        )
    )


def log_heatmap_error(
    *,
    request_id: str,
    store_id: str,
    error: Exception,
    processing_time_ms: int,
) -> None:
    """Emit a structured JSON log entry for unexpected heatmap failures."""
    logger.error(
        json.dumps(
            {
                "request_id": request_id,
                "endpoint": "/stores/{store_id}/heatmap",
                "store_id": store_id,
                "processing_time_ms": processing_time_ms,
                "error_type": error.__class__.__name__,
                "error_message": str(error),
            }
        ),
        exc_info=True,
    )


@router.get(
    "/stores/{store_id}/heatmap",
    response_model=list[ZoneHeatmapItem],
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "No events found for store",
            "content": {
                "application/json": {"example": {"message": "No events found for store"}}
            },
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "example": {
                        "success": False,
                        "message": "Internal server error",
                    }
                }
            },
        },
    },
)
def get_store_heatmap(
    store_id: str,
    db: Annotated[Session, Depends(get_db)],
    start_time: Annotated[datetime | None, Query()] = None,
    end_time: Annotated[datetime | None, Query()] = None,
) -> list[ZoneHeatmapItem] | JSONResponse:
    """Return per-zone heatmap analytics generated from persisted events."""
    request_id = str(uuid.uuid4())
    started_at = time.perf_counter()

    try:
        service = HeatmapService(db)
        response = service.get_store_heatmap(
            store_id=store_id,
            start_time=start_time,
            end_time=end_time,
        )
    except Exception as exc:
        db.rollback()
        processing_time_ms = int((time.perf_counter() - started_at) * 1000)
        log_heatmap_error(
            request_id=request_id,
            store_id=store_id,
            error=exc,
            processing_time_ms=processing_time_ms,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "message": "Internal server error"},
        )

    if response is None:
        processing_time_ms = int((time.perf_counter() - started_at) * 1000)
        log_heatmap_result(
            request_id=request_id,
            store_id=store_id,
            start_time=start_time,
            end_time=end_time,
            processing_time_ms=processing_time_ms,
            status_code=status.HTTP_404_NOT_FOUND,
        )
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"message": "No events found for store"},
        )

    processing_time_ms = int((time.perf_counter() - started_at) * 1000)
    log_heatmap_result(
        request_id=request_id,
        store_id=store_id,
        start_time=start_time,
        end_time=end_time,
        processing_time_ms=processing_time_ms,
        status_code=status.HTTP_200_OK,
    )
    return response
