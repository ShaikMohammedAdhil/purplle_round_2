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
from app.session_service import (
    FunnelResponse,
    SessionRebuildRequest,
    SessionRebuildResponse,
    SessionService,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Funnel"])


def log_funnel_result(
    *,
    request_id: str,
    store_id: str,
    endpoint: str,
    processing_time_ms: int,
    status_code: int,
) -> None:
    """Emit a structured JSON log entry for session and funnel requests."""
    logger.info(
        json.dumps(
            {
                "request_id": request_id,
                "store_id": store_id,
                "endpoint": endpoint,
                "processing_time_ms": processing_time_ms,
                "status_code": status_code,
            }
        )
    )


def log_funnel_error(
    *,
    request_id: str,
    store_id: str,
    endpoint: str,
    error: Exception,
    processing_time_ms: int,
) -> None:
    """Emit a structured JSON log entry for unexpected funnel failures."""
    logger.error(
        json.dumps(
            {
                "request_id": request_id,
                "endpoint": endpoint,
                "store_id": store_id,
                "error_type": error.__class__.__name__,
                "error_message": str(error),
                "processing_time_ms": processing_time_ms,
            }
        ),
        exc_info=True,
    )


@router.post("/sessions/rebuild", response_model=SessionRebuildResponse)
def rebuild_sessions(
    request: SessionRebuildRequest,
    db: Annotated[Session, Depends(get_db)],
) -> SessionRebuildResponse | JSONResponse:
    """Reconstruct visitor sessions for a store from historical event data."""
    request_id = str(uuid.uuid4())
    started_at = time.perf_counter()
    endpoint = "/sessions/rebuild"

    try:
        service = SessionService(db)
        response = service.rebuild_sessions(request.store_id)
    except Exception as exc:
        db.rollback()
        processing_time_ms = int((time.perf_counter() - started_at) * 1000)
        log_funnel_error(
            request_id=request_id,
            store_id=request.store_id,
            endpoint=endpoint,
            error=exc,
            processing_time_ms=processing_time_ms,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "message": "Internal server error"},
        )

    processing_time_ms = int((time.perf_counter() - started_at) * 1000)
    log_funnel_result(
        request_id=request_id,
        store_id=request.store_id,
        endpoint=endpoint,
        processing_time_ms=processing_time_ms,
        status_code=status.HTTP_200_OK,
    )

    return response


@router.get(
    "/stores/{store_id}/funnel",
    response_model=FunnelResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "No events found for store",
            "content": {
                "application/json": {"example": {"message": "No events found for store"}}
            },
        }
    },
)
def get_store_funnel(
    store_id: str,
    db: Annotated[Session, Depends(get_db)],
    start_time: Annotated[datetime | None, Query()] = None,
    end_time: Annotated[datetime | None, Query()] = None,
) -> FunnelResponse | JSONResponse:
    """Return customer journey funnel analytics for a store."""
    request_id = str(uuid.uuid4())
    started_at = time.perf_counter()
    endpoint = "/stores/{store_id}/funnel"

    try:
        service = SessionService(db)
        funnel = service.get_funnel(
            store_id=store_id,
            start_time=start_time,
            end_time=end_time,
        )
    except Exception as exc:
        processing_time_ms = int((time.perf_counter() - started_at) * 1000)
        log_funnel_error(
            request_id=request_id,
            store_id=store_id,
            endpoint=endpoint,
            error=exc,
            processing_time_ms=processing_time_ms,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "message": "Internal server error"},
        )

    if funnel is None:
        processing_time_ms = int((time.perf_counter() - started_at) * 1000)
        log_funnel_result(
            request_id=request_id,
            store_id=store_id,
            endpoint=endpoint,
            processing_time_ms=processing_time_ms,
            status_code=status.HTTP_404_NOT_FOUND,
        )
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"message": "No events found for store"},
        )

    processing_time_ms = int((time.perf_counter() - started_at) * 1000)
    log_funnel_result(
        request_id=request_id,
        store_id=store_id,
        endpoint=endpoint,
        processing_time_ms=processing_time_ms,
        status_code=status.HTTP_200_OK,
    )

    return funnel
