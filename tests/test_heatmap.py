# PROMPT:
#
# Build Phase 11 tests for the Store Intelligence heatmap analytics endpoint,
# including normal responses, multiple zones, empty store behavior, date
# filtering, invalid store handling, and unexpected service failure handling.
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
from app.db import Base, get_db
from app.heatmap_service import HeatmapService
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


def dt(day: int, hour: int = 10, minute: int = 0) -> datetime:
    return datetime(2026, 3, day, hour, minute, tzinfo=timezone.utc)


def make_event(
    event_id: str,
    *,
    visitor_id: str = "VIS_001",
    event_type: EventType = EventType.ZONE_ENTER,
    timestamp: datetime | None = None,
    zone_id: str = "MAKEUP_ZONE",
    dwell_ms: int = 0,
    store_id: str = "STORE_001",
    is_staff: bool = False,
) -> Event:
    return Event(
        event_id=event_id,
        store_id=store_id,
        camera_id="CAM_001",
        visitor_id=visitor_id,
        event_type=event_type.value,
        timestamp=timestamp or dt(3),
        zone_id=zone_id,
        dwell_ms=dwell_ms,
        is_staff=is_staff,
        confidence=0.95,
        metadata_json="{}",
    )


def seed(db_session: Session, *events: Event) -> None:
    db_session.add_all(events)
    db_session.commit()


def by_zone(payload: list[dict]) -> dict[str, dict]:
    return {item["zone_id"]: item for item in payload}


def test_heatmap_normal_response(client: TestClient, db_session: Session) -> None:
    seed(
        db_session,
        make_event("evt_enter", visitor_id="VIS_001"),
        make_event(
            "evt_dwell",
            visitor_id="VIS_001",
            event_type=EventType.ZONE_DWELL,
            dwell_ms=45000,
        ),
    )

    response = client.get("/stores/STORE_001/heatmap")

    assert response.status_code == 200
    payload = response.json()
    assert payload == [
        {
            "zone_id": "MAKEUP_ZONE",
            "visit_count": 1,
            "unique_visitors": 1,
            "avg_dwell_ms": 45000.0,
            "heat_score": 100,
        }
    ]


def test_heatmap_multiple_zones(client: TestClient, db_session: Session) -> None:
    seed(
        db_session,
        make_event("evt_makeup_1", visitor_id="VIS_001", zone_id="MAKEUP_ZONE"),
        make_event("evt_makeup_2", visitor_id="VIS_002", zone_id="MAKEUP_ZONE"),
        make_event(
            "evt_makeup_dwell",
            visitor_id="VIS_001",
            event_type=EventType.ZONE_DWELL,
            zone_id="MAKEUP_ZONE",
            dwell_ms=20000,
        ),
        make_event("evt_skin_1", visitor_id="VIS_003", zone_id="SKINCARE_ZONE"),
        make_event(
            "evt_skin_dwell",
            visitor_id="VIS_003",
            event_type=EventType.ZONE_DWELL,
            zone_id="SKINCARE_ZONE",
            dwell_ms=10000,
        ),
    )

    response = client.get("/stores/STORE_001/heatmap")

    assert response.status_code == 200
    zones = by_zone(response.json())
    assert zones["MAKEUP_ZONE"]["visit_count"] == 2
    assert zones["MAKEUP_ZONE"]["unique_visitors"] == 2
    assert zones["MAKEUP_ZONE"]["heat_score"] == 100
    assert zones["SKINCARE_ZONE"]["visit_count"] == 1
    assert zones["SKINCARE_ZONE"]["heat_score"] == 25


def test_empty_store_returns_404(client: TestClient) -> None:
    response = client.get("/stores/STORE_001/heatmap")

    assert response.status_code == 404
    assert response.json() == {"message": "No events found for store"}


def test_date_filtering(client: TestClient, db_session: Session) -> None:
    seed(
        db_session,
        make_event("evt_old", visitor_id="VIS_OLD", timestamp=dt(1)),
        make_event("evt_new", visitor_id="VIS_NEW", timestamp=dt(15)),
    )

    response = client.get(
        "/stores/STORE_001/heatmap",
        params={
            "start_time": "2026-03-10T00:00:00Z",
            "end_time": "2026-03-20T23:59:59Z",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["visit_count"] == 1
    assert payload[0]["unique_visitors"] == 1


def test_invalid_store_returns_404(client: TestClient, db_session: Session) -> None:
    seed(db_session, make_event("evt_valid_store"))

    response = client.get("/stores/INVALID_STORE/heatmap")

    assert response.status_code == 404
    assert response.json() == {"message": "No events found for store"}


def test_error_handling_returns_500(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_get_store_heatmap(
        self: HeatmapService,
        *,
        store_id: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> None:
        raise RuntimeError("heatmap service failed")

    monkeypatch.setattr(
        HeatmapService,
        "get_store_heatmap",
        failing_get_store_heatmap,
    )

    response = client.get("/stores/STORE_001/heatmap")

    assert response.status_code == 500
    assert response.json() == {
        "success": False,
        "message": "Internal server error",
    }
