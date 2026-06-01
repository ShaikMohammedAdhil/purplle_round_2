from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Select, desc, distinct, func, select
from sqlalchemy.orm import Session

from app.models import Event
from app.schemas import EventType


class StoreMetricsResponse(BaseModel):
    """Business intelligence metrics for a retail store."""

    store_id: str
    unique_visitors: int
    entries: int
    exits: int
    staff_entries: int
    average_dwell_time_seconds: float
    peak_hour: str
    queue_joins: int
    queue_abandons: int
    abandonment_rate: float
    conversion_rate: float


class MetricsService:
    """Provides aggregate analytics over persisted CCTV event data."""

    def __init__(self, db: Session) -> None:
        """Initialize the service with a request-scoped database session."""
        self.db = db

    def get_store_metrics(
        self,
        *,
        store_id: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> StoreMetricsResponse | None:
        """Return aggregate store metrics for the requested time window."""
        filters = self._build_filters(
            store_id=store_id,
            start_time=start_time,
            end_time=end_time,
        )

        total_events = self._scalar_int(
            select(func.count()).select_from(Event).where(*filters)
        )
        if total_events == 0:
            return None

        unique_visitors = self._scalar_int(
            select(func.count(distinct(Event.visitor_id))).where(
                *filters,
                Event.is_staff.is_(False),
            )
        )
        entries = self._count_events(
            filters=filters,
            event_type=EventType.ENTRY,
            is_staff=False,
        )
        exits = self._count_events(
            filters=filters,
            event_type=EventType.EXIT,
            is_staff=False,
        )
        staff_entries = self._count_events(
            filters=filters,
            event_type=EventType.ENTRY,
            is_staff=True,
        )
        queue_joins = self._count_events(
            filters=filters,
            event_type=EventType.BILLING_QUEUE_JOIN,
        )
        queue_abandons = self._count_events(
            filters=filters,
            event_type=EventType.BILLING_QUEUE_ABANDON,
        )

        return StoreMetricsResponse(
            store_id=store_id,
            unique_visitors=unique_visitors,
            entries=entries,
            exits=exits,
            staff_entries=staff_entries,
            average_dwell_time_seconds=self.calculate_average_dwell(filters),
            peak_hour=self.calculate_peak_hour(filters),
            queue_joins=queue_joins,
            queue_abandons=queue_abandons,
            abandonment_rate=self.calculate_abandonment_rate(
                queue_joins=queue_joins,
                queue_abandons=queue_abandons,
            ),
            conversion_rate=self.calculate_conversion_rate(
                filters=filters,
                total_unique_visitors=unique_visitors,
            ),
        )

    def calculate_peak_hour(self, filters: list[Any]) -> str:
        """Return the hour with the highest event volume formatted as HH:00."""
        event_hour = func.strftime("%H", Event.timestamp).label("event_hour")
        statement = (
            select(event_hour, func.count(Event.event_id).label("event_count"))
            .where(*filters)
            .group_by(event_hour)
            .order_by(desc("event_count"), event_hour)
            .limit(1)
        )
        row = self.db.execute(statement).first()
        if row is None or row.event_hour is None:
            return "00:00"
        return f"{row.event_hour}:00"

    def calculate_conversion_rate(
        self,
        *,
        filters: list[Any],
        total_unique_visitors: int,
    ) -> float:
        """Calculate the percentage of visitors with purchase-like queue activity."""
        if total_unique_visitors == 0:
            return 0.0

        converted_visitors = self._scalar_int(
            select(func.count(distinct(Event.visitor_id))).where(
                *filters,
                Event.is_staff.is_(False),
                Event.event_type == EventType.BILLING_QUEUE_JOIN.value,
            )
        )
        return round((converted_visitors / total_unique_visitors) * 100, 2)

    def calculate_abandonment_rate(
        self,
        *,
        queue_joins: int,
        queue_abandons: int,
    ) -> float:
        """Calculate billing queue abandonment percentage."""
        if queue_joins == 0:
            return 0.0
        return round((queue_abandons / queue_joins) * 100, 2)

    def calculate_average_dwell(self, filters: list[Any]) -> float:
        """Calculate average dwell time in seconds from dwell milliseconds."""
        average_dwell_ms = self.db.execute(
            select(func.avg(Event.dwell_ms)).where(
                *filters,
                Event.dwell_ms.is_not(None),
            )
        ).scalar_one_or_none()
        if average_dwell_ms is None:
            return 0.0
        return round(float(average_dwell_ms) / 1000, 2)

    def _count_events(
        self,
        *,
        filters: list[Any],
        event_type: EventType,
        is_staff: bool | None = None,
    ) -> int:
        """Count events by type with an optional staff filter."""
        statement = select(func.count()).select_from(Event).where(
            *filters,
            Event.event_type == event_type.value,
        )
        if is_staff is not None:
            statement = statement.where(Event.is_staff.is_(is_staff))
        return self._scalar_int(statement)

    def _scalar_int(self, statement: Select[tuple[Any, ...]]) -> int:
        """Execute an aggregate query and coerce the scalar result to int."""
        value = self.db.execute(statement).scalar_one()
        return int(value or 0)

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
