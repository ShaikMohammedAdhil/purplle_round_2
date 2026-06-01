# PROMPT:
#
# Add Phase 10 coverage for pipeline-to-API event upload, including batching,
# retry behavior, partial success handling, and mock ingestion API integration.
#
# CHANGES MADE:
#
# Used an injectable post function so tests validate upload behavior without
# requiring a live FastAPI server.

import json
from pathlib import Path
from typing import Any

from pipeline.config import PipelineConfig
from pipeline.ingestion_client import IngestionClient, load_events


def build_event(event_id: str) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "store_id": "STORE_001",
        "camera_id": "CAM_001",
        "visitor_id": "VISITOR_000001",
        "event_type": "ENTRY",
        "timestamp": "2026-03-03T14:22:10Z",
        "zone_id": "ENTRY_ZONE",
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.95,
        "metadata": {"session_seq": 1},
    }


def test_event_upload_logic_posts_to_ingest_endpoint() -> None:
    calls: list[tuple[str, list[dict[str, Any]], float]] = []

    def fake_post(
        endpoint: str,
        batch: list[dict[str, Any]],
        timeout: float,
    ) -> tuple[int, dict[str, Any]]:
        calls.append((endpoint, batch, timeout))
        return 200, {
            "success": True,
            "inserted_count": len(batch),
            "duplicate_count": 0,
            "failed_count": 0,
            "failed_events": [],
        }

    client = IngestionClient(
        api_base_url="http://api.test",
        batch_size=10,
        timeout=3.5,
        max_retries=0,
        post_function=fake_post,
    )

    summary = client.upload_events([build_event("evt_001")])

    assert summary.uploaded_count == 1
    assert summary.failed_count == 0
    assert calls[0][0] == "http://api.test/events/ingest"
    assert calls[0][2] == 3.5


def test_batching_splits_events_by_configured_size() -> None:
    batch_sizes: list[int] = []

    def fake_post(
        endpoint: str,
        batch: list[dict[str, Any]],
        timeout: float,
    ) -> tuple[int, dict[str, Any]]:
        batch_sizes.append(len(batch))
        return 200, {
            "inserted_count": len(batch),
            "duplicate_count": 0,
            "failed_count": 0,
        }

    client = IngestionClient(
        api_base_url="http://api.test",
        batch_size=2,
        timeout=5.0,
        max_retries=0,
        post_function=fake_post,
    )

    summary = client.upload_events([build_event(f"evt_{index}") for index in range(5)])

    assert batch_sizes == [2, 2, 1]
    assert summary.batch_count == 3
    assert summary.uploaded_count == 5


def test_retry_behavior_recovers_after_transient_failure() -> None:
    attempts = 0

    def flaky_post(
        endpoint: str,
        batch: list[dict[str, Any]],
        timeout: float,
    ) -> tuple[int, dict[str, Any]]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("timed out")
        return 200, {
            "inserted_count": len(batch),
            "duplicate_count": 0,
            "failed_count": 0,
        }

    client = IngestionClient(
        api_base_url="http://api.test",
        batch_size=10,
        timeout=5.0,
        max_retries=2,
        post_function=flaky_post,
    )

    summary = client.upload_events([build_event("evt_retry")])

    assert attempts == 2
    assert summary.uploaded_count == 1
    assert summary.failed_count == 0


def test_partial_success_response_counts_failures() -> None:
    def fake_post(
        endpoint: str,
        batch: list[dict[str, Any]],
        timeout: float,
    ) -> tuple[int, dict[str, Any]]:
        return 200, {
            "success": True,
            "inserted_count": 1,
            "duplicate_count": 1,
            "failed_count": 1,
            "failed_events": [{"event_id": "evt_bad", "reason": "invalid event type"}],
        }

    client = IngestionClient(
        api_base_url="http://api.test",
        batch_size=10,
        timeout=5.0,
        max_retries=0,
        post_function=fake_post,
    )

    summary = client.upload_events(
        [build_event("evt_ok"), build_event("evt_duplicate"), build_event("evt_bad")]
    )

    assert summary.uploaded_count == 1
    assert summary.duplicate_count == 1
    assert summary.failed_count == 1


def test_mock_api_integration_uploads_events_from_jsonl(tmp_path: Path) -> None:
    events_file = tmp_path / "events.jsonl"
    events = [build_event("evt_file_001"), build_event("evt_file_002")]
    events_file.write_text(
        "\n".join(json.dumps(event) for event in events) + "\n",
        encoding="utf-8",
    )

    def fake_post(
        endpoint: str,
        batch: list[dict[str, Any]],
        timeout: float,
    ) -> tuple[int, dict[str, Any]]:
        return 200, {
            "inserted_count": len(batch),
            "duplicate_count": 0,
            "failed_count": 0,
        }

    client = IngestionClient.from_config(
        PipelineConfig(
            api_base_url="http://api.test",
            api_batch_size=100,
            api_timeout=5.0,
            max_retries=1,
        ),
        post_function=fake_post,
    )

    loaded_events = load_events(events_file)
    summary = client.upload_events_file(events_file)

    assert loaded_events == events
    assert summary.attempted_count == 2
    assert summary.uploaded_count == 2
    assert summary.failed_count == 0
