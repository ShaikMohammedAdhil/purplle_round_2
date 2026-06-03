from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Select, case, distinct, func, select
from sqlalchemy.orm import Session

from app.models import Event
from app.schemas import EventType


class ZoneHeatmapItem(BaseModel):
    """Heatmap analytics for one store zone."""

    zone_id: str
    visit_count: int
    unique_visitors: int
    avg_dwell_ms: float
    heat_score: int


class HeatmapService:
    """Provides zone-level heatmap analytics from persisted event data."""

    def __init__(self, db: Session) -> None:
        """Initialize the service with a request-scoped database session."""
        self.db = db

    def get_store_heatmap(
        self,
        *,
        store_id: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[ZoneHeatmapItem] | None:
        """Return per-zone heatmap analytics for the requested store and time window."""
        filters = self._build_filters(
            store_id=store_id,
            start_time=start_time,
            end_time=end_time,
        )

        if self._event_count(filters) == 0:
            return None

        rows = self.db.execute(self._heatmap_statement(filters)).all()
        if not rows:
            return []

        max_raw_score = max(self._raw_heat_score(row.visit_count, row.avg_dwell_ms) for row in rows)
        return [
            ZoneHeatmapItem(
                zone_id=row.zone_id,
                visit_count=int(row.visit_count or 0),
                unique_visitors=int(row.unique_visitors or 0),
                avg_dwell_ms=round(float(row.avg_dwell_ms or 0), 2),
                heat_score=self._normalized_heat_score(
                    raw_score=self._raw_heat_score(row.visit_count, row.avg_dwell_ms),
                    max_raw_score=max_raw_score,
                ),
            )
            for row in rows
        ]

    def _heatmap_statement(self, filters: list[Any]) -> Select[tuple[Any, ...]]:
        """Build the aggregate SQLAlchemy statement for zone heatmap analytics."""
        dwell_value = case(
            (Event.dwell_ms > 0, Event.dwell_ms),
            else_=None,
        )
        visit_value = case(
            (Event.event_type == EventType.ZONE_ENTER.value, Event.event_id),
            else_=None,
        )
        return (
            select(
                Event.zone_id.label("zone_id"),
                func.count(visit_value).label("visit_count"),
                func.count(distinct(Event.visitor_id)).label("unique_visitors"),
                func.avg(dwell_value).label("avg_dwell_ms"),
            )
            .where(
                *filters,
                Event.zone_id.is_not(None),
                Event.is_staff.is_(False),
            )
            .group_by(Event.zone_id)
            .order_by(Event.zone_id)
        )

    def _event_count(self, filters: list[Any]) -> int:
        """Return the number of events matching the requested store and time window."""
        value = self.db.execute(
            select(func.count()).select_from(Event).where(*filters)
        ).scalar_one()
        return int(value or 0)

    @staticmethod
    def _raw_heat_score(visit_count: int | None, avg_dwell_ms: float | None) -> float:
        """Combine visit frequency and dwell into a raw heat score."""
        visits = float(visit_count or 0)
        dwell_weight = max(float(avg_dwell_ms or 0), 1.0)
        return visits * dwell_weight

    @staticmethod
    def _normalized_heat_score(*, raw_score: float, max_raw_score: float) -> int:
        """Normalize a raw heat score into the inclusive 0-100 range."""
        if max_raw_score <= 0:
            return 0
        return round((raw_score / max_raw_score) * 100)

    @staticmethod
    def _build_filters(
        *,
        store_id: str,
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> list[Any]:
        """Build reusable filters for store and optional time bounds."""
        filters: list[Any] = [Event.store_id == store_id]
        if start_time is not None:
            filters.append(Event.timestamp >= start_time)
        if end_time is not None:
            filters.append(Event.timestamp <= end_time)
        return filters
