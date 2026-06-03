import json
from datetime import datetime, timezone
from pathlib import Path

from pipeline.config import PipelineConfig
from pipeline.emit import BusinessEventGenerator, VisitorIdRegistry, Zone, ZoneMap
from pipeline.tracker import TrackedDetection


def tracked(
    frame: int,
    bbox: list[float],
    *,
    track_id: int = 1,
    timestamp_ms: int | None = None,
) -> TrackedDetection:
    return TrackedDetection(
        track_id=track_id,
        frame=frame,
        timestamp_ms=timestamp_ms if timestamp_ms is not None else frame * 1000,
        bbox=bbox,
        confidence=0.93,
    )


def zone_map() -> ZoneMap:
    return ZoneMap(
        [
            Zone("ENTRY_ZONE", 0, 0, 100, 100),
            Zone("MAKEUP_ZONE", 101, 0, 220, 100),
            Zone("BILLING_ZONE", 221, 0, 340, 100),
            Zone("EXIT_ZONE", 341, 0, 460, 100),
        ]
    )


def generator(tmp_path: Path) -> BusinessEventGenerator:
    return BusinessEventGenerator(
        config=PipelineConfig(
            output_directory=str(tmp_path),
            zone_dwell_threshold_ms=2000,
        ),
        zone_map=zone_map(),
        base_time=datetime(2026, 3, 3, tzinfo=timezone.utc),
    )


def test_visitor_id_creation() -> None:
    registry = VisitorIdRegistry()

    assert registry.visitor_id_for(12) == "VISITOR_000001"
    assert registry.visitor_id_for(12) == "VISITOR_000001"
    assert registry.visitor_id_for(99) == "VISITOR_000002"


def test_prefixed_visitor_id_creation() -> None:
    registry = VisitorIdRegistry(prefix="CAM_1_")

    assert registry.visitor_id_for(12) == "CAM_1_VISITOR_000001"
    assert registry.visitor_id_for(99) == "CAM_1_VISITOR_000002"


def test_zone_transitions_generate_entry_and_zone_events(tmp_path: Path) -> None:
    events = generator(tmp_path).generate(
        [
            tracked(1, [10, 10, 40, 40], timestamp_ms=0),
            tracked(2, [130, 10, 170, 40], timestamp_ms=1000),
        ]
    )

    event_types = [event.event_type for event in events]

    assert "ENTRY" in event_types
    assert event_types.count("ZONE_ENTER") == 2
    assert "ZONE_EXIT" in event_types


def test_zone_dwell_event_generation(tmp_path: Path) -> None:
    events = generator(tmp_path).generate(
        [
            tracked(1, [130, 10, 170, 40], timestamp_ms=0),
            tracked(2, [132, 10, 172, 40], timestamp_ms=2500),
        ]
    )

    dwell_events = [event for event in events if event.event_type == "ZONE_DWELL"]

    assert len(dwell_events) == 1
    assert dwell_events[0].zone_id == "MAKEUP_ZONE"
    assert dwell_events[0].dwell_ms == 2500


def test_jsonl_output(tmp_path: Path) -> None:
    event_generator = generator(tmp_path)
    events = event_generator.generate([tracked(1, [10, 10, 40, 40], timestamp_ms=0)])

    output_file = event_generator.write_events(events)
    lines = output_file.read_text(encoding="utf-8").splitlines()

    assert output_file.name == "events.jsonl"
    assert len(lines) == len(events)
    assert json.loads(lines[0])["visitor_id"] == "VISITOR_000001"


def test_reentry_detection(tmp_path: Path) -> None:
    events = generator(tmp_path).generate(
        [
            tracked(1, [10, 10, 40, 40], timestamp_ms=0),
            tracked(2, [370, 10, 420, 40], timestamp_ms=1000),
            tracked(3, [10, 10, 40, 40], timestamp_ms=3000),
        ]
    )

    assert "EXIT" in [event.event_type for event in events]
    assert "REENTRY" in [event.event_type for event in events]


def test_billing_queue_events(tmp_path: Path) -> None:
    events = generator(tmp_path).generate(
        [
            tracked(1, [10, 10, 40, 40], timestamp_ms=0),
            tracked(2, [250, 10, 300, 40], timestamp_ms=1000),
            tracked(3, [130, 10, 170, 40], timestamp_ms=5000),
        ]
    )

    event_types = [event.event_type for event in events]

    assert "BILLING_QUEUE_JOIN" in event_types
    assert "BILLING_QUEUE_ABANDON" in event_types


def test_billing_exit_conversion_does_not_abandon(tmp_path: Path) -> None:
    events = generator(tmp_path).generate(
        [
            tracked(1, [10, 10, 40, 40], timestamp_ms=0),
            tracked(2, [250, 10, 300, 40], timestamp_ms=1000),
            tracked(3, [370, 10, 420, 40], timestamp_ms=5000),
        ]
    )

    event_types = [event.event_type for event in events]

    assert "BILLING_QUEUE_JOIN" in event_types
    assert "EXIT" in event_types
    assert "BILLING_QUEUE_ABANDON" not in event_types
