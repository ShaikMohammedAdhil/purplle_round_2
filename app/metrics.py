import json
import logging
import time
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.analytics_service import MetricsService, StoreMetricsResponse
from app.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Metrics"])


def log_metrics_result(
    *,
    request_id: str,
    store_id: str,
    start_time: datetime | None,
    end_time: datetime | None,
    processing_time_ms: int,
    status_code: int,
) -> None:
    """Emit a structured JSON log entry for a metrics request."""
    logger.info(
        json.dumps(
            {
                "request_id": request_id,
                "store_id": store_id,
                "start_time": start_time.isoformat() if start_time else None,
                "end_time": end_time.isoformat() if end_time else None,
                "processing_time_ms": processing_time_ms,
                "status_code": status_code,
            }
        )
    )


@router.get(
    "/stores/{store_id}/metrics",
    response_model=StoreMetricsResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "No events found for store",
            "content": {
                "application/json": {"example": {"message": "No events found for store"}}
            },
        }
    },
)
def get_store_metrics(
    store_id: str,
    db: Annotated[Session, Depends(get_db)],
    start_time: Annotated[datetime | None, Query()] = None,
    end_time: Annotated[datetime | None, Query()] = None,
) -> StoreMetricsResponse | JSONResponse:
    """Return store-level analytics metrics generated from persisted events."""
    request_id = str(uuid.uuid4())
    started_at = time.perf_counter()

    service = MetricsService(db)
    metrics = service.get_store_metrics(
        store_id=store_id,
        start_time=start_time,
        end_time=end_time,
    )

    if metrics is None:
        processing_time_ms = int((time.perf_counter() - started_at) * 1000)
        log_metrics_result(
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
    log_metrics_result(
        request_id=request_id,
        store_id=store_id,
        start_time=start_time,
        end_time=end_time,
        processing_time_ms=processing_time_ms,
        status_code=status.HTTP_200_OK,
    )

    return metrics
