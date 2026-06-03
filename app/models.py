from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.db import Base


class UTCDateTime(TypeDecorator[datetime]):
    """Store datetimes as UTC and restore timezone info for SQLite compatibility."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Event(Base):
    """CCTV analytics event emitted by the store intelligence pipeline."""

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_store_id", "store_id"),
        Index("ix_events_visitor_id", "visitor_id"),
        Index("ix_events_event_type", "event_type"),
        Index("ix_events_timestamp", "timestamp"),
    )

    event_id: Mapped[str] = mapped_column(String, primary_key=True, unique=True)
    store_id: Mapped[str] = mapped_column(String, nullable=False)
    camera_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    visitor_id: Mapped[str] = mapped_column(String, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    zone_id: Mapped[str | None] = mapped_column(String, nullable=True)
    dwell_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_staff: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=func.now(),
    )


class VisitorSession(Base):
    """Visitor session used for funnel, conversion, and abandonment analytics."""

    __tablename__ = "visitor_sessions"

    session_id: Mapped[str] = mapped_column(String, primary_key=True)
    visitor_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    store_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    entry_time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    exit_time: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    converted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    purchase_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    purchase_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_dwell_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=func.now(),
    )


class POSTransaction(Base):
    """Point-of-sale transaction for later correlation with visitor sessions."""

    __tablename__ = "pos_transactions"

    transaction_id: Mapped[str] = mapped_column(String, primary_key=True)
    store_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    basket_value: Mapped[float] = mapped_column(Float, nullable=False)
    visitor_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=func.now(),
    )


class Anomaly(Base):
    """Active or historical anomaly detected in store operations."""

    __tablename__ = "anomalies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[str] = mapped_column(String, nullable=False)
    anomaly_type: Mapped[str] = mapped_column(String, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_action: Mapped[str] = mapped_column(Text, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
