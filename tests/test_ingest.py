# PROMPT:
#
# Create pytest coverage for the Store Intelligence API Phase 3 ingestion endpoint,
# including valid inserts, duplicates, invalid confidence, batch-size validation,
# partial success, missing required fields, and invalid event types.
#
# CHANGES MADE:
#
# Used an isolated in-memory SQLite database with FastAPI dependency overrides so
# tests do not read from or write to the local data/store.db file.

from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.db import Base, get_db
from app.main import app


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
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

    def override_get_db() -> Generator[Session, None, None]:
        db = testing_session_local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()
        test_client.close()
        Base.metadata.drop_all(bind=engine)


def build_event(event_id: str = "evt_001", **overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event_id": event_id,
        "store_id": "STORE_001",
        "camera_id": "CAM_ENTRY_01",
        "visitor_id": "VIS_001",
        "event_type": "ENTRY",
        "timestamp": "2026-03-03T14:22:10Z",
        "zone_id": None,
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.95,
        "metadata": {"session_seq": 1},
    }
    event.update(overrides)
    return event


def test_valid_event_is_inserted(client: TestClient) -> None:
    response = client.post("/events/ingest", json=[build_event()])

    assert response.status_code == 200
    payload = response.json()
    assert payload["inserted_count"] == 1
    assert payload["duplicate_count"] == 0
    assert payload["failed_count"] == 0


def test_duplicate_event_is_counted_as_duplicate(client: TestClient) -> None:
    first_response = client.post("/events/ingest", json=[build_event()])
    second_response = client.post("/events/ingest", json=[build_event()])

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    payload = second_response.json()
    assert payload["inserted_count"] == 0
    assert payload["duplicate_count"] == 1
    assert payload["failed_count"] == 0


def test_invalid_confidence_fails_event(client: TestClient) -> None:
    response = client.post(
        "/events/ingest",
        json=[build_event(confidence=1.2)],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["inserted_count"] == 0
    assert payload["failed_count"] == 1
    assert payload["failed_events"][0]["reason"] == "invalid confidence"


def test_batch_larger_than_500_returns_http_400(client: TestClient) -> None:
    oversized_batch = [build_event(event_id=f"evt_{index}") for index in range(501)]

    response = client.post("/events/ingest", json=oversized_batch)

    assert response.status_code == 400
    assert response.json() == {
        "success": False,
        "message": "Maximum batch size exceeded",
    }


def test_partial_success_batch(client: TestClient) -> None:
    response = client.post(
        "/events/ingest",
        json=[
            build_event(event_id="evt_valid"),
            build_event(event_id="evt_invalid", confidence=-0.1),
        ],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["inserted_count"] == 1
    assert payload["failed_count"] == 1
    assert payload["failed_events"][0]["event_id"] == "evt_invalid"


def test_missing_required_field_fails_event(client: TestClient) -> None:
    invalid_event = build_event()
    invalid_event.pop("store_id")

    response = client.post("/events/ingest", json=[invalid_event])

    assert response.status_code == 200
    payload = response.json()
    assert payload["inserted_count"] == 0
    assert payload["failed_count"] == 1
    assert payload["failed_events"][0]["reason"] == "missing field"


def test_invalid_event_type_fails_event(client: TestClient) -> None:
    response = client.post(
        "/events/ingest",
        json=[build_event(event_type="INVALID_EVENT")],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["inserted_count"] == 0
    assert payload["failed_count"] == 1
    assert payload["failed_events"][0]["reason"] == "invalid event type"
