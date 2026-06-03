import json
from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.db import Base, get_db
from app.main import app
from app.models import Event
from pipeline.config import PipelineConfig
from pipeline.detect import PersonDetectionPipeline
from pipeline.emit import BusinessEventGenerator, Zone, ZoneMap
from pipeline.ingestion_client import IngestionClient
from pipeline.tracker import TrackedDetection
from pipeline.tracker import load_detections, track_detections


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


def zone_map() -> ZoneMap:
    return ZoneMap(
        [
            Zone("ENTRY_ZONE", 0, 0, 100, 100),
            Zone("MAKEUP_ZONE", 101, 0, 220, 100),
            Zone("BILLING_ZONE", 221, 0, 340, 100),
            Zone("EXIT_ZONE", 341, 0, 460, 100),
        ]
    )


def tracked(frame: int, bbox: list[float], timestamp_ms: int) -> TrackedDetection:
    return TrackedDetection(
        track_id=7,
        frame=frame,
        timestamp_ms=timestamp_ms,
        bbox=bbox,
        confidence=0.94,
    )


def generated_pipeline_events() -> list[dict[str, object]]:
    generator = BusinessEventGenerator(
        config=PipelineConfig(store_id="STORE_001", camera_id="CAM_001"),
        zone_map=zone_map(),
        base_time=datetime(2026, 3, 3, tzinfo=timezone.utc),
    )
    events = generator.generate(
        [
            tracked(1, [10, 10, 40, 40], 0),
            tracked(2, [130, 10, 170, 40], 1000),
            tracked(3, [250, 10, 300, 40], 2000),
            tracked(4, [370, 10, 420, 40], 3000),
        ]
    )
    return [event.to_dict() for event in events]


def ingest_generated_events(client: TestClient) -> list[dict[str, object]]:
    events = generated_pipeline_events()
    response = client.post("/events/ingest", json=events)

    assert response.status_code == 200
    assert response.json()["inserted_count"] == len(events)
    return events


def test_pipeline_event_ingests_and_feeds_analytics(
    client: TestClient,
    db_session: Session,
) -> None:
    events = ingest_generated_events(client)

    event_count = db_session.scalar(select(func.count()).select_from(Event))
    assert event_count == len(events)

    stored = db_session.scalars(
        select(Event).where(Event.event_type == "ENTRY")
    ).one()
    metadata = json.loads(stored.metadata_json)
    assert metadata["track_id"] == 7
    assert metadata["frame"] == 1

    rebuild = client.post("/sessions/rebuild", json={"store_id": "STORE_001"})
    assert rebuild.status_code == 200

    metrics = client.get("/stores/STORE_001/metrics")
    assert metrics.status_code == 200
    payload = metrics.json()
    assert payload["entries"] == 1
    assert payload["queue_joins"] == 1
    assert payload["queue_abandons"] == 0
    assert payload["conversion_rate"] == 100.0


def test_required_endpoint_smoke_paths(client: TestClient) -> None:
    ingest_generated_events(client)
    rebuild = client.post("/sessions/rebuild", json={"store_id": "STORE_001"})
    assert rebuild.status_code == 200

    checks = [
        client.get("/health"),
        client.get("/health/"),
        client.get("/stores/STORE_001/metrics"),
        client.get("/stores/STORE_001/funnel"),
        client.get("/stores/STORE_001/anomalies"),
        client.get("/stores/STORE_001/queue-status"),
        client.get("/stores/STORE_001/heatmap"),
    ]

    assert all(response.status_code == 200 for response in checks)


class FakeCapture:
    """OpenCV VideoCapture double for the mocked Phase 12 flow."""

    def __init__(self, frames: list[list[float]]) -> None:
        self.frames = frames
        self.index = 0

    def isOpened(self) -> bool:
        return True

    def read(self) -> tuple[bool, Any | None]:
        if self.index >= len(self.frames):
            return False, None
        frame = self.frames[self.index]
        self.index += 1
        return True, frame

    def get(self, prop: int) -> float:
        from pipeline import detect

        values = {
            detect.cv2.CAP_PROP_FPS: 10.0,
            detect.cv2.CAP_PROP_FRAME_COUNT: float(len(self.frames)),
            detect.cv2.CAP_PROP_FRAME_WIDTH: 460.0,
            detect.cv2.CAP_PROP_FRAME_HEIGHT: 100.0,
            detect.cv2.CAP_PROP_POS_FRAMES: float(self.index),
        }
        return values.get(prop, 0.0)

    def release(self) -> None:
        return None


class FakeScalar:
    def __init__(self, value: int | float) -> None:
        self.value = value

    def item(self) -> int | float:
        return self.value


class FakeTensor:
    def __init__(self, values: list[float]) -> None:
        self.values = values

    def tolist(self) -> list[float]:
        return self.values


class FakeBox:
    def __init__(self, bbox: list[float]) -> None:
        self.cls = [FakeScalar(0)]
        self.conf = [FakeScalar(0.95)]
        self.xyxy = [FakeTensor(bbox)]


class FakeResult:
    def __init__(self, bbox: list[float]) -> None:
        self.boxes = [FakeBox(bbox)]


class FakeModel:
    def __call__(self, frame: list[float], verbose: bool = False) -> list[FakeResult]:
        return [FakeResult(frame)]


def test_mocked_video_to_analytics_flow(
    client: TestClient,
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"mock video")

    frames = [
        [10, 10, 40, 40],
        [130, 10, 170, 40],
        [250, 10, 300, 40],
        [370, 10, 420, 40],
    ]

    from pipeline import detect

    monkeypatch.setattr(
        detect.cv2,
        "VideoCapture",
        lambda path: FakeCapture(frames=frames),
    )

    config = PipelineConfig(
        output_directory=str(tmp_path / "out"),
        tracker_max_distance=500.0,
        store_id="STORE_001",
        camera_id="CAM_001",
    )
    detection_summary = PersonDetectionPipeline(config, model=FakeModel()).run(video_path)
    detections = load_detections(detection_summary.output_file)
    tracked_items = track_detections(detections, config)

    generator = BusinessEventGenerator(
        config=config,
        zone_map=zone_map(),
        base_time=datetime(2026, 3, 3, tzinfo=timezone.utc),
    )
    events = generator.generate(tracked_items)
    events_file = generator.write_events(events)

    client_uploader = IngestionClient.from_config(
        config,
        post_function=lambda endpoint, batch, timeout: (
            lambda response: (response.status_code, response.json())
        )(client.post("/events/ingest", json=batch)),
    )
    upload_summary = client_uploader.upload_events_file(events_file)

    assert detection_summary.detection_count == 4
    assert len({item.track_id for item in tracked_items}) == 1
    assert upload_summary.uploaded_count == len(events)
    assert db_session.scalar(select(func.count()).select_from(Event)) == len(events)

    rebuild = client.post("/sessions/rebuild", json={"store_id": "STORE_001"})
    assert rebuild.status_code == 200

    for path in (
        "/stores/STORE_001/metrics",
        "/stores/STORE_001/funnel",
        "/stores/STORE_001/anomalies",
        "/stores/STORE_001/queue-status",
        "/stores/STORE_001/heatmap",
    ):
        response = client.get(path)
        assert response.status_code == 200
