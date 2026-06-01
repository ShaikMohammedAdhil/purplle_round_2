# PROMPT:
#
# Build Phase 6 tests for anomaly detection, queue monitoring, store health
# analytics, 404 handling, and unexpected service failure handling.
#
# CHANGES MADE:
#
# Used an isolated in-memory SQLite database with FastAPI dependency overrides so
# tests run independently from the local data/store.db file.

from collections.abc import Generator
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.anomaly_service import AnomalyService
from app.db import Base, get_db
from app.main import app
from app.models import Event, VisitorSession
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


def dt(day: int, hour: int = 10, minute: int = 0) -> datetime:
    return datetime(2026, 3, day, hour, minute, tzinfo=timezone.utc)


def make_event(
    event_id: str,
    *,
    visitor_id: str = "VIS_001",
    event_type: EventType = EventType.ENTRY,
    timestamp: datetime | None = None,
    store_id: str = "STORE_001",
) -> Event:
    return Event(
        event_id=event_id,
        store_id=store_id,
        camera_id="CAM_001",
        visitor_id=visitor_id,
        event_type=event_type.value,
        timestamp=timestamp or dt(3),
        zone_id=None,
        dwell_ms=0,
        is_staff=False,
        confidence=0.95,
        metadata_json="{}",
    )


def make_session(
    session_id: str,
    *,
    visitor_id: str = "VIS_001",
    total_dwell_ms: int = 0,
    converted: bool = False,
    store_id: str = "STORE_001",
) -> VisitorSession:
    return VisitorSession(
        session_id=session_id,
        visitor_id=visitor_id,
        store_id=store_id,
        entry_time=dt(3),
        exit_time=dt(3, 11),
        converted=converted,
        purchase_count=1 if converted else 0,
        purchase_amount=0.0,
        total_dwell_ms=total_dwell_ms,
        is_active=False,
    )


def seed(db_session: Session, *objects: object) -> None:
    db_session.add_all(objects)
    db_session.commit()


def anomaly_types(payload: dict) -> set[str]:
    return {item["type"] for item in payload["anomalies"]}


def test_long_dwell_anomaly(client: TestClient, db_session: Session) -> None:
    seed(
        db_session,
        make_event("evt_entry"),
        make_session("session_001", total_dwell_ms=31 * 60 * 1000),
    )

    response = client.get("/stores/STORE_001/anomalies")

    assert response.status_code == 200
    payload = response.json()
    assert "LONG_DWELL_TIME" in anomaly_types(payload)


def test_queue_congestion_anomaly(client: TestClient, db_session: Session) -> None:
    events = [make_event("evt_entry")]
    events.extend(
        make_event(
            f"evt_join_{index}",
            visitor_id=f"VIS_JOIN_{index}",
            event_type=EventType.BILLING_QUEUE_JOIN,
        )
        for index in range(51)
    )
    events.extend(
        make_event(
            f"evt_abandon_{index}",
            visitor_id=f"VIS_ABANDON_{index}",
            event_type=EventType.BILLING_QUEUE_ABANDON,
        )
        for index in range(11)
    )
    seed(db_session, *events)

    response = client.get("/stores/STORE_001/anomalies")

    assert response.status_code == 200
    payload = response.json()
    assert "QUEUE_CONGESTION" in anomaly_types(payload)


def test_traffic_spike_anomaly(client: TestClient, db_session: Session) -> None:
    seed(
        db_session,
        make_event("evt_hist_1", visitor_id="VIS_HIST_1", timestamp=dt(1)),
        make_event("evt_hist_2", visitor_id="VIS_HIST_2", timestamp=dt(2)),
        make_event("evt_current_1", visitor_id="VIS_CUR_1", timestamp=dt(3)),
        make_event("evt_current_2", visitor_id="VIS_CUR_2", timestamp=dt(3, 10, 5)),
        make_event("evt_current_3", visitor_id="VIS_CUR_3", timestamp=dt(3, 10, 10)),
    )

    response = client.get(
        "/stores/STORE_001/anomalies",
        params={
            "start_time": "2026-03-03T00:00:00Z",
            "end_time": "2026-03-03T23:59:59Z",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert "TRAFFIC_SPIKE" in anomaly_types(payload)


def test_low_conversion_anomaly(client: TestClient, db_session: Session) -> None:
    seed(
        db_session,
        make_event("evt_entry_1", visitor_id="VIS_001"),
        make_event("evt_entry_2", visitor_id="VIS_002"),
    )

    response = client.get("/stores/STORE_001/anomalies")

    assert response.status_code == 200
    payload = response.json()
    assert "LOW_CONVERSION" in anomaly_types(payload)


def test_empty_store_anomaly(client: TestClient, db_session: Session) -> None:
    seed(db_session, make_event("evt_old", timestamp=dt(1)))

    response = client.get(
        "/stores/STORE_001/anomalies",
        params={
            "start_time": "2026-03-04T00:00:00Z",
            "end_time": "2026-03-04T23:59:59Z",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["anomaly_count"] == 1
    assert payload["anomalies"][0]["type"] == "EMPTY_STORE"


def test_queue_status_normal(client: TestClient, db_session: Session) -> None:
    seed(
        db_session,
        *[
            make_event(
                f"evt_join_{index}",
                visitor_id=f"VIS_{index}",
                event_type=EventType.BILLING_QUEUE_JOIN,
            )
            for index in range(10)
        ],
    )

    response = client.get("/stores/STORE_001/queue-status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["abandonment_rate"] == 0.0
    assert payload["status"] == "NORMAL"


def test_queue_status_busy(client: TestClient, db_session: Session) -> None:
    events = [
        make_event(
            f"evt_join_{index}",
            visitor_id=f"VIS_{index}",
            event_type=EventType.BILLING_QUEUE_JOIN,
        )
        for index in range(10)
    ]
    events.append(
        make_event(
            "evt_abandon",
            visitor_id="VIS_001",
            event_type=EventType.BILLING_QUEUE_ABANDON,
        )
    )
    seed(db_session, *events)

    response = client.get("/stores/STORE_001/queue-status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["abandonment_rate"] == 10.0
    assert payload["status"] == "BUSY"


def test_queue_status_congested(client: TestClient, db_session: Session) -> None:
    events = [
        make_event(
            f"evt_join_{index}",
            visitor_id=f"VIS_{index}",
            event_type=EventType.BILLING_QUEUE_JOIN,
        )
        for index in range(10)
    ]
    events.extend(
        make_event(
            f"evt_abandon_{index}",
            visitor_id=f"VIS_{index}",
            event_type=EventType.BILLING_QUEUE_ABANDON,
        )
        for index in range(2)
    )
    seed(db_session, *events)

    response = client.get("/stores/STORE_001/queue-status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["abandonment_rate"] == 20.0
    assert payload["status"] == "CONGESTED"


def test_store_with_no_events_returns_404(client: TestClient) -> None:
    response = client.get("/stores/STORE_404/anomalies")

    assert response.status_code == 404
    assert response.json() == {"message": "No events found for store"}


def test_unexpected_service_failure_returns_500(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_get_store_anomalies(
        self: AnomalyService,
        *,
        store_id: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> None:
        raise RuntimeError("anomaly service failed")

    monkeypatch.setattr(
        AnomalyService,
        "get_store_anomalies",
        failing_get_store_anomalies,
    )

    response = client.get("/stores/STORE_001/anomalies")

    assert response.status_code == 500
    assert response.json() == {
        "success": False,
        "message": "Internal server error",
    }
