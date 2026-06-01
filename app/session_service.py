from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import Select, case, distinct, func, or_, select
from sqlalchemy.orm import Session

from app.models import Event, VisitorSession
from app.schemas import EventType


class SessionRebuildRequest(BaseModel):
    """Request body for rebuilding visitor sessions from historical events."""

    store_id: str


class SessionRebuildResponse(BaseModel):
    """Summary returned after rebuilding visitor sessions."""

    store_id: str
    sessions_created: int
    sessions_updated: int


class FunnelResponse(BaseModel):
    """Customer journey funnel metrics for a store."""

    store_id: str
    entered: int
    engaged: int
    queue_visitors: int
    converted: int
    engagement_rate: float
    queue_rate: float
    conversion_rate: float


@dataclass
class SessionReplayStats:
    """Counters collected while replaying the event stream."""

    sessions_created: int = 0
    sessions_updated: int = 0
    reentry_count: int = 0


class SessionService:
    """Builds visitor sessions and funnel analytics from persisted events."""

    def __init__(self, db: Session) -> None:
        """Initialize the service with a request-scoped database session."""
        self.db = db
        self.replay_stats = SessionReplayStats()

    def create_or_update_session(self, event: Event) -> VisitorSession | None:
        """Apply one event to the visitor session state machine."""
        if event.is_staff:
            return None

        if event.event_type == EventType.ENTRY.value:
            return self.handle_entry(event)
        if event.event_type == EventType.EXIT.value:
            return self.handle_exit(event)
        if event.event_type == EventType.ZONE_DWELL.value:
            return self.calculate_session_dwell(event)
        if event.event_type == EventType.BILLING_QUEUE_JOIN.value:
            return self.mark_conversion(event)

        return None

    def handle_entry(self, event: Event) -> VisitorSession:
        """Create a visitor session for an entry event when none is active."""
        active_session = self._get_active_session(
            visitor_id=event.visitor_id,
            store_id=event.store_id,
        )
        if active_session is not None:
            return active_session

        if self.detect_reentry(event):
            self.replay_stats.reentry_count += 1

        session = VisitorSession(
            session_id=str(uuid4()),
            visitor_id=event.visitor_id,
            store_id=event.store_id,
            entry_time=event.timestamp,
            exit_time=None,
            converted=False,
            purchase_count=0,
            purchase_amount=0.0,
            total_dwell_ms=0,
            is_active=True,
        )
        self.db.add(session)
        self.db.flush()
        self.replay_stats.sessions_created += 1
        return session

    def handle_exit(self, event: Event) -> VisitorSession | None:
        """Close an active visitor session from an exit event."""
        session = self._get_active_session(
            visitor_id=event.visitor_id,
            store_id=event.store_id,
        )
        if session is None:
            return None

        session.exit_time = event.timestamp
        session.total_dwell_ms = max(
            session.total_dwell_ms,
            self._milliseconds_between(session.entry_time, event.timestamp),
        )
        session.is_active = False
        self.db.flush()
        self.replay_stats.sessions_updated += 1
        return session

    def calculate_session_dwell(self, event: Event) -> VisitorSession | None:
        """Accumulate dwell milliseconds on the active visitor session."""
        session = self._get_active_session(
            visitor_id=event.visitor_id,
            store_id=event.store_id,
        )
        if session is None:
            return None

        session.total_dwell_ms += max(event.dwell_ms or 0, 0)
        self.db.flush()
        self.replay_stats.sessions_updated += 1
        return session

    def mark_conversion(self, event: Event) -> VisitorSession | None:
        """Mark an active visitor session as converted from queue activity."""
        session = self._get_active_session(
            visitor_id=event.visitor_id,
            store_id=event.store_id,
        )
        if session is None:
            return None

        session.converted = True
        session.purchase_count += 1
        self.db.flush()
        self.replay_stats.sessions_updated += 1
        return session

    def detect_reentry(self, event: Event) -> bool:
        """Return true when an entry follows a previously closed session."""
        statement = (
            select(VisitorSession.session_id)
            .where(
                VisitorSession.visitor_id == event.visitor_id,
                VisitorSession.store_id == event.store_id,
                VisitorSession.is_active.is_(False),
                VisitorSession.exit_time.is_not(None),
                VisitorSession.exit_time <= event.timestamp,
            )
            .limit(1)
        )
        return self.db.execute(statement).scalar_one_or_none() is not None

    def rebuild_sessions(self, store_id: str) -> SessionRebuildResponse:
        """Rebuild sessions for a store by replaying events in timestamp order."""
        self.replay_stats = SessionReplayStats()
        self._delete_existing_sessions(store_id)

        events = self.db.scalars(
            select(Event)
            .where(Event.store_id == store_id)
            .order_by(
                Event.timestamp.asc(),
                self._replay_order(),
                Event.event_id.asc(),
            )
        ).all()

        for event in events:
            self.create_or_update_session(event)

        self.db.commit()

        return SessionRebuildResponse(
            store_id=store_id,
            sessions_created=self.replay_stats.sessions_created,
            sessions_updated=self.replay_stats.sessions_updated,
        )

    def get_funnel(
        self,
        *,
        store_id: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> FunnelResponse | None:
        """Calculate visitor funnel metrics for a store and optional time window."""
        filters = self._event_filters(
            store_id=store_id,
            start_time=start_time,
            end_time=end_time,
        )
        event_count = self._scalar_int(
            select(func.count()).select_from(Event).where(*filters)
        )
        if event_count == 0:
            return None

        entered = self._distinct_event_visitors(
            filters=filters,
            event_types=[EventType.ENTRY],
        )
        engaged = self._distinct_event_visitors(
            filters=filters,
            event_types=[EventType.ZONE_ENTER, EventType.ZONE_DWELL],
        )
        queue_visitors = self._distinct_event_visitors(
            filters=filters,
            event_types=[EventType.BILLING_QUEUE_JOIN],
        )
        converted = self._converted_visitors(
            store_id=store_id,
            start_time=start_time,
            end_time=end_time,
        )

        return FunnelResponse(
            store_id=store_id,
            entered=entered,
            engaged=engaged,
            queue_visitors=queue_visitors,
            converted=converted,
            engagement_rate=self._rate(engaged, entered),
            queue_rate=self._rate(queue_visitors, entered),
            conversion_rate=self._rate(converted, entered),
        )

    def _get_active_session(
        self,
        *,
        visitor_id: str,
        store_id: str,
    ) -> VisitorSession | None:
        """Return the newest active session for a visitor in a store."""
        statement = (
            select(VisitorSession)
            .where(
                VisitorSession.visitor_id == visitor_id,
                VisitorSession.store_id == store_id,
                VisitorSession.is_active.is_(True),
            )
            .order_by(VisitorSession.entry_time.desc())
            .limit(1)
        )
        return self.db.execute(statement).scalar_one_or_none()

    def _delete_existing_sessions(self, store_id: str) -> None:
        """Remove existing derived sessions for a store before rebuilding."""
        sessions = self.db.scalars(
            select(VisitorSession).where(VisitorSession.store_id == store_id)
        ).all()
        for session in sessions:
            self.db.delete(session)
        self.db.flush()

    def _distinct_event_visitors(
        self,
        *,
        filters: list[Any],
        event_types: list[EventType],
    ) -> int:
        """Count distinct non-staff visitors for the selected event types."""
        values = [event_type.value for event_type in event_types]
        return self._scalar_int(
            select(func.count(distinct(Event.visitor_id))).where(
                *filters,
                Event.is_staff.is_(False),
                Event.event_type.in_(values),
            )
        )

    def _converted_visitors(
        self,
        *,
        store_id: str,
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> int:
        """Count distinct converted visitors from stored visitor sessions."""
        filters: list[Any] = [
            VisitorSession.store_id == store_id,
            VisitorSession.converted.is_(True),
        ]
        if start_time is not None:
            filters.append(
                or_(
                    VisitorSession.exit_time >= start_time,
                    VisitorSession.exit_time.is_(None),
                )
            )
        if end_time is not None:
            filters.append(VisitorSession.entry_time <= end_time)

        return self._scalar_int(
            select(func.count(distinct(VisitorSession.visitor_id))).where(*filters)
        )

    @staticmethod
    def _event_filters(
        *,
        store_id: str,
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> list[Any]:
        """Build store and time filters for event-based funnel metrics."""
        filters: list[Any] = [Event.store_id == store_id]
        if start_time is not None:
            filters.append(Event.timestamp >= start_time)
        if end_time is not None:
            filters.append(Event.timestamp <= end_time)
        return filters

    @staticmethod
    def _milliseconds_between(start: datetime, end: datetime) -> int:
        """Return the non-negative duration between two datetimes in milliseconds."""
        return max(int((end - start).total_seconds() * 1000), 0)

    @staticmethod
    def _rate(numerator: int, denominator: int) -> float:
        """Return a rounded percentage, guarding against division by zero."""
        if denominator == 0:
            return 0.0
        return round((numerator / denominator) * 100, 2)

    def _scalar_int(self, statement: Select[tuple[Any, ...]]) -> int:
        """Execute an aggregate query and coerce the scalar result to int."""
        value = self.db.execute(statement).scalar_one()
        return int(value or 0)

    @staticmethod
    def _replay_order() -> Any:
        """Return event-type precedence for deterministic same-timestamp replay."""
        return case(
            (Event.event_type == EventType.ENTRY.value, 0),
            (Event.event_type == EventType.ZONE_ENTER.value, 1),
            (Event.event_type == EventType.ZONE_DWELL.value, 2),
            (Event.event_type == EventType.BILLING_QUEUE_JOIN.value, 3),
            (Event.event_type == EventType.BILLING_QUEUE_ABANDON.value, 4),
            (Event.event_type == EventType.EXIT.value, 5),
            (Event.event_type == EventType.REENTRY.value, 6),
            else_=99,
        )
