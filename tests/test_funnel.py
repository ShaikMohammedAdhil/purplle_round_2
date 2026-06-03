# PROMPT:
#
# Build Phase 5 tests for visitor session tracking, session rebuild, funnel
# analytics, re-entry handling, engagement rate, and conversion rate.
#
# CHANGES MADE:
#
# Used an isolated in-memory SQLite database with FastAPI dependency overrides so
# tests do not depend on the local data/store.db file.

from collections.abc import Generator
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import funnel, models  # noqa: F401
from app.db import Base, get_db
from app.main import app
from app.models import Event, VisitorSession
from app.schemas import EventType
from app.session_service import SessionService


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


def dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 3, 3, hour, minute, tzinfo=timezone.utc)


def make_event(
    event_id: str,
    *,
    visitor_id: str = "VIS_001",
    event_type: EventType = EventType.ENTRY,
    timestamp: datetime | None = None,
    dwell_ms: int = 0,
    store_id: str = "STORE_001",
) -> Event:
    return Event(
        event_id=event_id,
        store_id=store_id,
        camera_id="CAM_001",
        visitor_id=visitor_id,
        event_type=event_type.value,
        timestamp=timestamp or dt(10),
        zone_id=None,
        dwell_ms=dwell_ms,
        is_staff=False,
        confidence=0.95,
        metadata_json="{}",
    )


def seed_events(db_session: Session, events: list[Event]) -> None:
    db_session.add_all(events)
    db_session.commit()


def get_only_session(db_session: Session) -> VisitorSession:
    return db_session.execute(select(VisitorSession)).scalar_one()


def test_session_creation_from_entry(db_session: Session) -> None:
    service = SessionService(db_session)
    service.create_or_update_session(make_event("evt_entry"))

    session = get_only_session(db_session)
    assert session.visitor_id == "VIS_001"
    assert session.store_id == "STORE_001"
    assert session.is_active is True
    assert session.entry_time == dt(10)


def test_session_closure_from_exit(db_session: Session) -> None:
    service = SessionService(db_session)
    service.create_or_update_session(make_event("evt_entry", timestamp=dt(10)))
    service.create_or_update_session(
        make_event("evt_exit", event_type=EventType.EXIT, timestamp=dt(10, 30))
    )

    session = get_only_session(db_session)
    assert session.is_active is False
    assert session.exit_time == dt(10, 30)
    assert session.total_dwell_ms == 1_800_000


def test_dwell_accumulation(db_session: Session) -> None:
    service = SessionService(db_session)
    service.create_or_update_session(make_event("evt_entry", timestamp=dt(10)))
    service.create_or_update_session(
        make_event(
            "evt_dwell_1",
            event_type=EventType.ZONE_DWELL,
            timestamp=dt(10, 5),
            dwell_ms=1_500,
        )
    )
    service.create_or_update_session(
        make_event(
            "evt_dwell_2",
            event_type=EventType.ZONE_DWELL,
            timestamp=dt(10, 10),
            dwell_ms=2_500,
        )
    )

    session = get_only_session(db_session)
    assert session.total_dwell_ms == 4_000


def test_conversion_tracking(db_session: Session) -> None:
    service = SessionService(db_session)
    service.create_or_update_session(make_event("evt_entry"))
    service.create_or_update_session(
        make_event("evt_queue", event_type=EventType.BILLING_QUEUE_JOIN)
    )

    session = get_only_session(db_session)
    assert session.converted is True
    assert session.purchase_count == 1


def test_reentry_handling(db_session: Session) -> None:
    service = SessionService(db_session)
    service.create_or_update_session(make_event("evt_entry_1", timestamp=dt(10)))
    service.create_or_update_session(
        make_event("evt_exit_1", event_type=EventType.EXIT, timestamp=dt(10, 20))
    )
    service.create_or_update_session(make_event("evt_entry_2", timestamp=dt(11)))

    sessions = db_session.execute(
        select(VisitorSession).order_by(VisitorSession.entry_time)
    ).scalars().all()
    assert len(sessions) == 2
    assert sessions[0].is_active is False
    assert sessions[1].is_active is True
    assert service.replay_stats.reentry_count == 1


def test_session_rebuild_endpoint(client: TestClient, db_session: Session) -> None:
    seed_events(
        db_session,
        [
            make_event("evt_entry", timestamp=dt(10)),
            make_event("evt_dwell", event_type=EventType.ZONE_DWELL, dwell_ms=5000),
            make_event("evt_queue", event_type=EventType.BILLING_QUEUE_JOIN),
            make_event("evt_exit", event_type=EventType.EXIT, timestamp=dt(10, 30)),
        ],
    )

    response = client.post("/sessions/rebuild", json={"store_id": "STORE_001"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["store_id"] == "STORE_001"
    assert payload["sessions_created"] == 1
    assert payload["sessions_updated"] == 3
    assert db_session.execute(select(VisitorSession)).scalars().first() is not None


def test_funnel_endpoint_success(client: TestClient, db_session: Session) -> None:
    seed_events(
        db_session,
        [
            make_event("evt_entry_1", visitor_id="VIS_001", event_type=EventType.ENTRY),
            make_event("evt_entry_2", visitor_id="VIS_002", event_type=EventType.ENTRY),
            make_event(
                "evt_zone",
                visitor_id="VIS_001",
                event_type=EventType.ZONE_ENTER,
            ),
            make_event(
                "evt_queue",
                visitor_id="VIS_001",
                event_type=EventType.BILLING_QUEUE_JOIN,
            ),
        ],
    )
    client.post("/sessions/rebuild", json={"store_id": "STORE_001"})

    response = client.get("/stores/STORE_001/funnel")

    assert response.status_code == 200
    payload = response.json()
    assert payload["store_id"] == "STORE_001"
    assert payload["entered"] == 2
    assert payload["engaged"] == 1
    assert payload["queue_visitors"] == 1
    assert payload["converted"] == 1


def test_funnel_404(client: TestClient) -> None:
    response = client.get("/stores/STORE_404/funnel")

    assert response.status_code == 404
    assert response.json() == {"message": "No events found for store"}


def test_engagement_rate_calculation(client: TestClient, db_session: Session) -> None:
    seed_events(
        db_session,
        [
            make_event("evt_entry_1", visitor_id="VIS_001", event_type=EventType.ENTRY),
            make_event("evt_entry_2", visitor_id="VIS_002", event_type=EventType.ENTRY),
            make_event(
                "evt_zone",
                visitor_id="VIS_001",
                event_type=EventType.ZONE_DWELL,
                dwell_ms=1000,
            ),
        ],
    )

    response = client.get("/stores/STORE_001/funnel")

    assert response.status_code == 200
    assert response.json()["engagement_rate"] == 50.0


def test_conversion_rate_calculation(client: TestClient, db_session: Session) -> None:
    seed_events(
        db_session,
        [
            make_event("evt_entry_1", visitor_id="VIS_001", event_type=EventType.ENTRY),
            make_event("evt_entry_2", visitor_id="VIS_002", event_type=EventType.ENTRY),
            make_event(
                "evt_queue",
                visitor_id="VIS_001",
                event_type=EventType.BILLING_QUEUE_JOIN,
            ),
        ],
    )
    client.post("/sessions/rebuild", json={"store_id": "STORE_001"})

    response = client.get("/stores/STORE_001/funnel")

    assert response.status_code == 200
    assert response.json()["conversion_rate"] == 50.0


def test_rebuild_failure_returns_http_500(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_rebuild(self: SessionService, store_id: str) -> None:
        raise RuntimeError("rebuild failed")

    monkeypatch.setattr(SessionService, "rebuild_sessions", failing_rebuild)

    response = client.post("/sessions/rebuild", json={"store_id": "STORE_001"})

    assert response.status_code == 500
    assert response.json() == {
        "success": False,
        "message": "Internal server error",
    }


def test_funnel_service_failure_returns_http_500(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_get_funnel(
        self: SessionService,
        *,
        store_id: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> None:
        raise RuntimeError("funnel failed")

    monkeypatch.setattr(SessionService, "get_funnel", failing_get_funnel)

    response = client.get("/stores/STORE_001/funnel")

    assert response.status_code == 500
    assert response.json() == {
        "success": False,
        "message": "Internal server error",
    }


def test_rebuild_failure_rolls_back(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rollback_called = False

    def failing_rebuild(self: SessionService, store_id: str) -> None:
        raise RuntimeError("rebuild failed")

    def rollback_spy() -> None:
        nonlocal rollback_called
        rollback_called = True

    monkeypatch.setattr(SessionService, "rebuild_sessions", failing_rebuild)
    monkeypatch.setattr(db_session, "rollback", rollback_spy)

    response = funnel.rebuild_sessions(
        request=funnel.SessionRebuildRequest(store_id="STORE_001"),
        db=db_session,
    )

    assert response.status_code == 500
    assert rollback_called is True
