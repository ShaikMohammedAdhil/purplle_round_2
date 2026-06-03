# PROMPT:
#
# Build pytest coverage for the Store Intelligence API Phase 4 metrics endpoint,
# including successful metrics, no events, conversion rate, abandonment rate,
# date filtering, peak hour, and average dwell calculation.
#
# CHANGES MADE:
#
# Used an isolated in-memory SQLite database and FastAPI dependency overrides so
# tests run independently from the local data/store.db file.

from collections.abc import Generator
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.db import Base, get_db
from app.main import app
from app.models import Event
from app.schemas import EventType


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session_local = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        class_=Session,
    )

    Base.metadata.create_all(bind=engine)
    session = testing_session_local()

    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(db_session: Session) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()
        test_client.close()


def make_event(
    event_id: str,
    *,
    visitor_id: str,
    event_type: EventType,
    timestamp: datetime,
    dwell_ms: int = 0,
    is_staff: bool = False,
    store_id: str = "STORE_001",
) -> Event:
    return Event(
        event_id=event_id,
        store_id=store_id,
        camera_id="CAM_001",
        visitor_id=visitor_id,
        event_type=event_type.value,
        timestamp=timestamp,
        zone_id=None,
        dwell_ms=dwell_ms,
        is_staff=is_staff,
        confidence=0.95,
        metadata_json="{}",
    )


def seed_events(db_session: Session, events: list[Event]) -> None:
    db_session.add_all(events)
    db_session.commit()


def dt(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 3, day, hour, minute, tzinfo=timezone.utc)


def test_metrics_endpoint_success(client: TestClient, db_session: Session) -> None:
    seed_events(
        db_session,
        [
            make_event(
                "evt_001",
                visitor_id="VIS_001",
                event_type=EventType.ENTRY,
                timestamp=dt(3, 14),
                dwell_ms=1000,
            ),
            make_event(
                "evt_002",
                visitor_id="VIS_002",
                event_type=EventType.EXIT,
                timestamp=dt(3, 15),
                dwell_ms=3000,
            ),
            make_event(
                "evt_003",
                visitor_id="STAFF_001",
                event_type=EventType.ENTRY,
                timestamp=dt(3, 15, 10),
                is_staff=True,
            ),
        ],
    )

    response = client.get("/stores/STORE_001/metrics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["store_id"] == "STORE_001"
    assert payload["unique_visitors"] == 2
    assert payload["entries"] == 1
    assert payload["exits"] == 1
    assert payload["staff_entries"] == 1


def test_no_events_returns_404(client: TestClient) -> None:
    response = client.get("/stores/STORE_404/metrics")

    assert response.status_code == 404
    assert response.json() == {"message": "No events found for store"}


def test_conversion_rate_calculation(client: TestClient, db_session: Session) -> None:
    seed_events(
        db_session,
        [
            make_event(
                "evt_001",
                visitor_id="VIS_001",
                event_type=EventType.ENTRY,
                timestamp=dt(3, 10),
            ),
            make_event(
                "evt_002",
                visitor_id="VIS_002",
                event_type=EventType.ENTRY,
                timestamp=dt(3, 10, 5),
            ),
            make_event(
                "evt_003",
                visitor_id="VIS_001",
                event_type=EventType.BILLING_QUEUE_JOIN,
                timestamp=dt(3, 10, 15),
            ),
        ],
    )

    response = client.get("/stores/STORE_001/metrics")

    assert response.status_code == 200
    assert response.json()["conversion_rate"] == 50.0


def test_abandonment_rate_calculation(client: TestClient, db_session: Session) -> None:
    seed_events(
        db_session,
        [
            make_event(
                "evt_001",
                visitor_id="VIS_001",
                event_type=EventType.BILLING_QUEUE_JOIN,
                timestamp=dt(3, 11),
            ),
            make_event(
                "evt_002",
                visitor_id="VIS_002",
                event_type=EventType.BILLING_QUEUE_JOIN,
                timestamp=dt(3, 11, 5),
            ),
            make_event(
                "evt_003",
                visitor_id="VIS_002",
                event_type=EventType.BILLING_QUEUE_ABANDON,
                timestamp=dt(3, 11, 10),
            ),
        ],
    )

    response = client.get("/stores/STORE_001/metrics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["queue_joins"] == 2
    assert payload["queue_abandons"] == 1
    assert payload["abandonment_rate"] == 50.0


def test_date_filtering(client: TestClient, db_session: Session) -> None:
    seed_events(
        db_session,
        [
            make_event(
                "evt_old",
                visitor_id="VIS_OLD",
                event_type=EventType.ENTRY,
                timestamp=dt(1, 9),
            ),
            make_event(
                "evt_in_range",
                visitor_id="VIS_NEW",
                event_type=EventType.ENTRY,
                timestamp=dt(15, 9),
            ),
        ],
    )

    response = client.get(
        "/stores/STORE_001/metrics",
        params={
            "start_time": "2026-03-10T00:00:00Z",
            "end_time": "2026-03-20T23:59:59Z",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["unique_visitors"] == 1
    assert payload["entries"] == 1


def test_peak_hour_calculation(client: TestClient, db_session: Session) -> None:
    seed_events(
        db_session,
        [
            make_event(
                "evt_001",
                visitor_id="VIS_001",
                event_type=EventType.ENTRY,
                timestamp=dt(3, 14),
            ),
            make_event(
                "evt_002",
                visitor_id="VIS_002",
                event_type=EventType.ENTRY,
                timestamp=dt(3, 15),
            ),
            make_event(
                "evt_003",
                visitor_id="VIS_003",
                event_type=EventType.EXIT,
                timestamp=dt(3, 15, 10),
            ),
        ],
    )

    response = client.get("/stores/STORE_001/metrics")

    assert response.status_code == 200
    assert response.json()["peak_hour"] == "15:00"


def test_average_dwell_calculation(client: TestClient, db_session: Session) -> None:
    seed_events(
        db_session,
        [
            make_event(
                "evt_001",
                visitor_id="VIS_001",
                event_type=EventType.ZONE_DWELL,
                timestamp=dt(3, 13),
                dwell_ms=1000,
            ),
            make_event(
                "evt_002",
                visitor_id="VIS_002",
                event_type=EventType.ZONE_DWELL,
                timestamp=dt(3, 13, 5),
                dwell_ms=3000,
            ),
        ],
    )

    response = client.get("/stores/STORE_001/metrics")

    assert response.status_code == 200
    assert response.json()["average_dwell_time_seconds"] == 2.0
