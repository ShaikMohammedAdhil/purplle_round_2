from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Select, distinct, func, select
from sqlalchemy.orm import Session

from app.models import Event, VisitorSession
from app.schemas import EventType
from app.session_service import SessionService


class AnomalyItem(BaseModel):
    """Single operational anomaly detected for a store."""

    type: str
    severity: str
    message: str
    value: float


class StoreAnomaliesResponse(BaseModel):
    """Response containing all detected anomalies for a store."""

    store_id: str
    anomaly_count: int
    anomalies: list[AnomalyItem]


class QueueStatusResponse(BaseModel):
    """Queue performance analytics for a store."""

    store_id: str
    queue_joins: int
    queue_abandons: int
    abandonment_rate: float
    status: str


class AnomalyService:
    """Detects store operations anomalies from events, sessions, and funnel data."""

    LONG_DWELL_THRESHOLD_MS = 30 * 60 * 1000
    QUEUE_JOIN_THRESHOLD = 50
    QUEUE_ABANDONMENT_THRESHOLD = 20.0
    LOW_CONVERSION_THRESHOLD = 5.0

    def __init__(self, db: Session) -> None:
        """Initialize the service with a request-scoped database session."""
        self.db = db
        self.session_service = SessionService(db)

    def get_store_anomalies(
        self,
        *,
        store_id: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> StoreAnomaliesResponse | None:
        """Return all anomalies detected for a store and optional time window."""
        if not self._store_has_events(store_id):
            return None

        filters = self._event_filters(
            store_id=store_id,
            start_time=start_time,
            end_time=end_time,
        )
        unique_visitors = self._unique_visitors(filters)

        anomalies: list[AnomalyItem] = []
        empty_store = self.detect_empty_store(unique_visitors)
        if empty_store is not None:
            anomalies.append(empty_store)
        else:
            anomalies.extend(
                anomaly
                for anomaly in (
                    self.detect_long_dwell(
                        store_id=store_id,
                        start_time=start_time,
                        end_time=end_time,
                    ),
                    self.detect_queue_congestion(filters),
                    self.detect_traffic_spike(
                        store_id=store_id,
                        current_visitor_count=unique_visitors,
                        start_time=start_time,
                        end_time=end_time,
                    ),
                    self.detect_low_conversion(
                        store_id=store_id,
                        start_time=start_time,
                        end_time=end_time,
                    ),
                )
                if anomaly is not None
            )

        return StoreAnomaliesResponse(
            store_id=store_id,
            anomaly_count=len(anomalies),
            anomalies=anomalies,
        )

    def get_queue_status(
        self,
        *,
        store_id: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> QueueStatusResponse | None:
        """Return queue status analytics for a store and optional time window."""
        if not self._store_has_events(store_id):
            return None

        filters = self._event_filters(
            store_id=store_id,
            start_time=start_time,
            end_time=end_time,
        )
        queue_joins = self._count_events(
            filters=filters,
            event_type=EventType.BILLING_QUEUE_JOIN,
        )
        queue_abandons = self._count_events(
            filters=filters,
            event_type=EventType.BILLING_QUEUE_ABANDON,
        )
        abandonment_rate = self._abandonment_rate(
            queue_joins=queue_joins,
            queue_abandons=queue_abandons,
        )

        return QueueStatusResponse(
            store_id=store_id,
            queue_joins=queue_joins,
            queue_abandons=queue_abandons,
            abandonment_rate=abandonment_rate,
            status=self._queue_status(abandonment_rate),
        )

    def detect_long_dwell(
        self,
        *,
        store_id: str,
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> AnomalyItem | None:
        """Detect sessions with average dwell time above 30 minutes."""
        filters: list[Any] = [VisitorSession.store_id == store_id]
        if start_time is not None:
            filters.append(VisitorSession.entry_time >= start_time)
        if end_time is not None:
            filters.append(VisitorSession.entry_time <= end_time)

        average_dwell_ms = self.db.execute(
            select(func.avg(VisitorSession.total_dwell_ms)).where(*filters)
        ).scalar_one_or_none()
        if average_dwell_ms is None:
            return None

        value = float(average_dwell_ms)
        if value <= self.LONG_DWELL_THRESHOLD_MS:
            return None

        return AnomalyItem(
            type="LONG_DWELL_TIME",
            severity="MEDIUM",
            message="Average session dwell time exceeds threshold",
            value=round(value / 60_000, 2),
        )

    def detect_queue_congestion(self, filters: list[Any]) -> AnomalyItem | None:
        """Detect queue congestion from joins and abandonment rate."""
        queue_joins = self._count_events(
            filters=filters,
            event_type=EventType.BILLING_QUEUE_JOIN,
        )
        queue_abandons = self._count_events(
            filters=filters,
            event_type=EventType.BILLING_QUEUE_ABANDON,
        )
        abandonment_rate = self._abandonment_rate(
            queue_joins=queue_joins,
            queue_abandons=queue_abandons,
        )
        if (
            queue_joins <= self.QUEUE_JOIN_THRESHOLD
            or abandonment_rate <= self.QUEUE_ABANDONMENT_THRESHOLD
        ):
            return None

        return AnomalyItem(
            type="QUEUE_CONGESTION",
            severity="HIGH",
            message="Queue abandonment exceeds threshold",
            value=abandonment_rate,
        )

    def detect_traffic_spike(
        self,
        *,
        store_id: str,
        current_visitor_count: int,
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> AnomalyItem | None:
        """Detect visitor traffic greater than 2x historical daily average."""
        if start_time is None or current_visitor_count == 0:
            return None

        event_day = func.date(Event.timestamp).label("event_day")
        statement = (
            select(func.count(distinct(Event.visitor_id)).label("daily_visitors"))
            .where(
                Event.store_id == store_id,
                Event.is_staff.is_(False),
                Event.event_type == EventType.ENTRY.value,
                Event.timestamp < start_time,
            )
            .group_by(event_day)
        )
        daily_counts = [int(value or 0) for value in self.db.execute(statement).scalars()]
        if not daily_counts:
            return None

        historical_average = sum(daily_counts) / len(daily_counts)
        if historical_average <= 0 or current_visitor_count <= historical_average * 2:
            return None

        return AnomalyItem(
            type="TRAFFIC_SPIKE",
            severity="MEDIUM",
            message="Current visitor count exceeds historical average",
            value=round(current_visitor_count, 2),
        )

    def detect_low_conversion(
        self,
        *,
        store_id: str,
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> AnomalyItem | None:
        """Detect conversion rate below the low conversion threshold."""
        funnel = self.session_service.get_funnel(
            store_id=store_id,
            start_time=start_time,
            end_time=end_time,
        )
        if funnel is None or funnel.entered == 0:
            return None
        if funnel.conversion_rate >= self.LOW_CONVERSION_THRESHOLD:
            return None

        return AnomalyItem(
            type="LOW_CONVERSION",
            severity="LOW",
            message="Conversion rate is below threshold",
            value=funnel.conversion_rate,
        )

    def detect_empty_store(self, unique_visitors: int) -> AnomalyItem | None:
        """Detect zero visitors during the requested period."""
        if unique_visitors != 0:
            return None
        return AnomalyItem(
            type="EMPTY_STORE",
            severity="LOW",
            message="No visitors detected during requested period",
            value=0.0,
        )

    def _store_has_events(self, store_id: str) -> bool:
        """Return true if the store has at least one persisted event."""
        return (
            self.db.execute(
                select(Event.event_id).where(Event.store_id == store_id).limit(1)
            ).scalar_one_or_none()
            is not None
        )

    def _unique_visitors(self, filters: list[Any]) -> int:
        """Count distinct non-staff entry visitors for the selected window."""
        return self._scalar_int(
            select(func.count(distinct(Event.visitor_id))).where(
                *filters,
                Event.is_staff.is_(False),
                Event.event_type == EventType.ENTRY.value,
            )
        )

    def _count_events(self, *, filters: list[Any], event_type: EventType) -> int:
        """Count events by type for the selected window."""
        return self._scalar_int(
            select(func.count()).select_from(Event).where(
                *filters,
                Event.event_type == event_type.value,
            )
        )

    @staticmethod
    def _event_filters(
        *,
        store_id: str,
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> list[Any]:
        """Build store and optional time filters for event analytics."""
        filters: list[Any] = [Event.store_id == store_id]
        if start_time is not None:
            filters.append(Event.timestamp >= start_time)
        if end_time is not None:
            filters.append(Event.timestamp <= end_time)
        return filters

    @staticmethod
    def _abandonment_rate(*, queue_joins: int, queue_abandons: int) -> float:
        """Calculate queue abandonment percentage."""
        if queue_joins == 0:
            return 0.0
        return round((queue_abandons / queue_joins) * 100, 2)

    @staticmethod
    def _queue_status(abandonment_rate: float) -> str:
        """Return queue health status from abandonment rate."""
        if abandonment_rate < 10:
            return "NORMAL"
        if abandonment_rate < 20:
            return "BUSY"
        return "CONGESTED"

    def _scalar_int(self, statement: Select[tuple[Any, ...]]) -> int:
        """Execute an aggregate query and coerce the scalar result to int."""
        value = self.db.execute(statement).scalar_one()
        return int(value or 0)
