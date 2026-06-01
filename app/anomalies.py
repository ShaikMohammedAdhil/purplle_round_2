import json
import logging
import time
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.anomaly_service import (
    AnomalyService,
    QueueStatusResponse,
    StoreAnomaliesResponse,
)
from app.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Anomalies"])


def log_anomaly_result(
    *,
    request_id: str,
    endpoint: str,
    store_id: str,
    processing_time_ms: int,
    status_code: int,
) -> None:
    """Emit a structured JSON log entry for anomaly request outcomes."""
    logger.info(
        json.dumps(
            {
                "request_id": request_id,
                "endpoint": endpoint,
                "store_id": store_id,
                "processing_time_ms": processing_time_ms,
                "status_code": status_code,
            }
        )
    )


def log_anomaly_error(
    *,
    request_id: str,
    endpoint: str,
    store_id: str,
    error: Exception,
    processing_time_ms: int,
) -> None:
    """Emit a structured JSON log entry for unexpected anomaly failures."""
    logger.error(
        json.dumps(
            {
                "request_id": request_id,
                "endpoint": endpoint,
                "store_id": store_id,
                "processing_time_ms": processing_time_ms,
                "error_type": error.__class__.__name__,
                "error_message": str(error),
            }
        ),
        exc_info=True,
    )


@router.get(
    "/stores/{store_id}/anomalies",
    response_model=StoreAnomaliesResponse,
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
def get_store_anomalies(
    store_id: str,
    db: Annotated[Session, Depends(get_db)],
    start_time: Annotated[datetime | None, Query()] = None,
    end_time: Annotated[datetime | None, Query()] = None,
) -> StoreAnomaliesResponse | JSONResponse:
    """Return operational anomalies detected for a store."""
    request_id = str(uuid.uuid4())
    started_at = time.perf_counter()
    endpoint = "/stores/{store_id}/anomalies"

    try:
        service = AnomalyService(db)
        response = service.get_store_anomalies(
            store_id=store_id,
            start_time=start_time,
            end_time=end_time,
        )
    except Exception as exc:
        db.rollback()
        processing_time_ms = int((time.perf_counter() - started_at) * 1000)
        log_anomaly_error(
            request_id=request_id,
            endpoint=endpoint,
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
        log_anomaly_result(
            request_id=request_id,
            endpoint=endpoint,
            store_id=store_id,
            processing_time_ms=processing_time_ms,
            status_code=status.HTTP_404_NOT_FOUND,
        )
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"message": "No events found for store"},
        )

    processing_time_ms = int((time.perf_counter() - started_at) * 1000)
    log_anomaly_result(
        request_id=request_id,
        endpoint=endpoint,
        store_id=store_id,
        processing_time_ms=processing_time_ms,
        status_code=status.HTTP_200_OK,
    )
    return response


@router.get(
    "/stores/{store_id}/queue-status",
    response_model=QueueStatusResponse,
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
def get_queue_status(
    store_id: str,
    db: Annotated[Session, Depends(get_db)],
    start_time: Annotated[datetime | None, Query()] = None,
    end_time: Annotated[datetime | None, Query()] = None,
) -> QueueStatusResponse | JSONResponse:
    """Return queue health analytics for a store."""
    request_id = str(uuid.uuid4())
    started_at = time.perf_counter()
    endpoint = "/stores/{store_id}/queue-status"

    try:
        service = AnomalyService(db)
        response = service.get_queue_status(
            store_id=store_id,
            start_time=start_time,
            end_time=end_time,
        )
    except Exception as exc:
        db.rollback()
        processing_time_ms = int((time.perf_counter() - started_at) * 1000)
        log_anomaly_error(
            request_id=request_id,
            endpoint=endpoint,
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
        log_anomaly_result(
            request_id=request_id,
            endpoint=endpoint,
            store_id=store_id,
            processing_time_ms=processing_time_ms,
            status_code=status.HTTP_404_NOT_FOUND,
        )
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"message": "No events found for store"},
        )

    processing_time_ms = int((time.perf_counter() - started_at) * 1000)
    log_anomaly_result(
        request_id=request_id,
        endpoint=endpoint,
        store_id=store_id,
        processing_time_ms=processing_time_ms,
        status_code=status.HTTP_200_OK,
    )
    return response
