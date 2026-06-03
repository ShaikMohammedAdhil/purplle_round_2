import json
from pathlib import Path
from typing import Any

import pytest

from pipeline import run_pipeline
from pipeline.config import PipelineConfig
from pipeline.detect import DetectionRunSummary, VideoMetadata, VideoProcessingError
from pipeline.emit import BusinessEvent, EventGenerationSummary
from pipeline.ingestion_client import EventUploadSummary
from pipeline.tracker import TrackedDetection


def test_multi_camera_directory_discovery(tmp_path: Path) -> None:
    (tmp_path / "cam1.mp4").write_text("video", encoding="utf-8")
    (tmp_path / "cam2.AVI").write_text("video", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("ignore", encoding="utf-8")
    (tmp_path / "store.mov").write_text("video", encoding="utf-8")

    videos = run_pipeline.discover_videos(tmp_path)

    assert [path.name for path in videos] == ["cam1.mp4", "cam2.AVI", "store.mov"]


def test_camera_id_assignment() -> None:
    assert run_pipeline.infer_camera_id(Path("cam1.mp4"), 1) == "CAM_1"
    assert run_pipeline.infer_camera_id(Path("store_cam5.mp4"), 2) == "CAM_5"
    assert run_pipeline.infer_camera_id(Path("front_door.mov"), 3) == "CAM_3"


def install_fake_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    uploaded_events: list[dict[str, Any]],
    *,
    failing_cameras: set[str] | None = None,
) -> None:
    failing_cameras = failing_cameras or set()

    class FakeDetectionPipeline:
        def __init__(self, config: PipelineConfig) -> None:
            self.config = config

        def run(self, video_path: str | Path) -> DetectionRunSummary:
            if self.config.camera_id in failing_cameras:
                raise VideoProcessingError("camera failed")

            output_dir = Path(self.config.output_directory)
            output_dir.mkdir(parents=True, exist_ok=True)
            detections_file = output_dir / "detections.jsonl"
            detections_file.write_text(
                json.dumps(
                    {
                        "frame": 1,
                        "timestamp_ms": 0,
                        "class_name": "person",
                        "confidence": 0.95,
                        "bbox": [1, 2, 3, 4],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            return DetectionRunSummary(
                video_path=str(video_path),
                metadata=VideoMetadata(
                    fps=30.0,
                    frame_count=1,
                    width=640,
                    height=480,
                    duration_ms=33,
                ),
                processed_frames=1,
                detection_count=1,
                output_file=str(detections_file),
                processing_time_ms=1,
            )

    def fake_load_detections(path: str | Path) -> list[object]:
        return [object()]

    def fake_track_detections(
        detections: list[object],
        config: PipelineConfig,
    ) -> list[TrackedDetection]:
        return [
            TrackedDetection(
                track_id=1,
                frame=1,
                timestamp_ms=0,
                bbox=[1.0, 2.0, 3.0, 4.0],
                confidence=0.95,
            )
        ]

    def fake_generate_events(
        tracked: list[TrackedDetection],
        config: PipelineConfig,
    ) -> tuple[list[BusinessEvent], EventGenerationSummary]:
        output_dir = Path(config.output_directory)
        output_dir.mkdir(parents=True, exist_ok=True)
        events_file = output_dir / "events.jsonl"
        event = BusinessEvent(
            event_id=f"evt_{config.camera_id}",
            store_id=config.store_id,
            camera_id=config.camera_id,
            visitor_id=f"{config.visitor_id_prefix}VISITOR_000001",
            event_type="ENTRY",
            timestamp="2026-03-03T14:22:10Z",
            zone_id="ENTRY_ZONE",
            dwell_ms=0,
            is_staff=False,
            confidence=0.95,
            metadata={"track_id": 1, "frame": 1},
        )
        events_file.write_text(event.to_json() + "\n", encoding="utf-8")
        return [event], EventGenerationSummary(
            visitors_created=1,
            events_generated=1,
            output_file=str(events_file),
        )

    class FakeIngestionClient:
        def __init__(self, config: PipelineConfig) -> None:
            self.config = config

        @classmethod
        def from_config(cls, config: PipelineConfig) -> "FakeIngestionClient":
            return cls(config)

        def upload_events_file(self, events_file: str | Path) -> EventUploadSummary:
            events = [
                json.loads(line)
                for line in Path(events_file).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            uploaded_events.extend(events)
            return EventUploadSummary(
                attempted_count=len(events),
                uploaded_count=len(events),
                duplicate_count=0,
                failed_count=0,
                batch_count=1 if events else 0,
                failed_batches=0,
            )

    monkeypatch.setattr(run_pipeline, "PersonDetectionPipeline", FakeDetectionPipeline)
    monkeypatch.setattr(run_pipeline, "load_detections", fake_load_detections)
    monkeypatch.setattr(run_pipeline, "track_detections", fake_track_detections)
    monkeypatch.setattr(run_pipeline, "generate_events", fake_generate_events)
    monkeypatch.setattr(run_pipeline, "IngestionClient", FakeIngestionClient)


def test_multi_camera_output_directories_and_aggregation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    (video_dir / "cam1.mp4").write_text("video", encoding="utf-8")
    (video_dir / "store_cam5.mp4").write_text("video", encoding="utf-8")
    uploaded_events: list[dict[str, Any]] = []
    install_fake_pipeline(monkeypatch, uploaded_events)

    output_root = tmp_path / "outputs"
    summary = run_pipeline.run_video_directory(
        video_dir=video_dir,
        base_config=PipelineConfig(
            output_directory=str(output_root),
            store_id="STORE_001",
        ),
    )

    assert summary.cameras_processed == 2
    assert summary.cameras_failed == 0
    assert summary.total_tracks == 2
    assert summary.total_visitors == 2
    assert summary.total_events == 2
    assert summary.uploaded == 2
    assert (output_root / "CAM_1" / "detections.jsonl").exists()
    assert (output_root / "CAM_1" / "events.jsonl").exists()
    assert (output_root / "CAM_5" / "detections.jsonl").exists()
    assert (output_root / "CAM_5" / "events.jsonl").exists()


def test_one_camera_failure_does_not_stop_other_cameras(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    (video_dir / "cam1.mp4").write_text("video", encoding="utf-8")
    (video_dir / "cam2.mp4").write_text("video", encoding="utf-8")
    uploaded_events: list[dict[str, Any]] = []
    install_fake_pipeline(monkeypatch, uploaded_events, failing_cameras={"CAM_2"})

    summary = run_pipeline.run_video_directory(
        video_dir=video_dir,
        base_config=PipelineConfig(
            output_directory=str(tmp_path / "outputs"),
            store_id="STORE_001",
        ),
    )

    assert summary.cameras_processed == 1
    assert summary.cameras_failed == 1
    assert summary.total_events == 1
    assert len(uploaded_events) == 1
    failed_camera = [
        camera
        for camera in summary.camera_summaries
        if camera["camera_id"] == "CAM_2"
    ][0]
    assert failed_camera["errors"][0]["type"] == "VideoProcessingError"


def test_upload_uses_per_camera_event_camera_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    (video_dir / "cam1.mp4").write_text("video", encoding="utf-8")
    (video_dir / "cam2.mp4").write_text("video", encoding="utf-8")
    uploaded_events: list[dict[str, Any]] = []
    install_fake_pipeline(monkeypatch, uploaded_events)

    run_pipeline.run_video_directory(
        video_dir=video_dir,
        base_config=PipelineConfig(
            output_directory=str(tmp_path / "outputs"),
            store_id="STORE_001",
        ),
    )

    assert {event["camera_id"] for event in uploaded_events} == {"CAM_1", "CAM_2"}
    assert {event["visitor_id"] for event in uploaded_events} == {
        "CAM_1_VISITOR_000001",
        "CAM_2_VISITOR_000001",
    }


def test_video_dir_cli_prints_store_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    (video_dir / "cam1.mp4").write_text("video", encoding="utf-8")
    uploaded_events: list[dict[str, Any]] = []
    install_fake_pipeline(monkeypatch, uploaded_events)

    exit_code = run_pipeline.main(
        [
            "--video-dir",
            str(video_dir),
            "--output-dir",
            str(tmp_path / "outputs"),
            "--store-id",
            "STORE_001",
        ]
    )

    printed = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert printed["store_id"] == "STORE_001"
    assert printed["cameras_processed"] == 1
    assert printed["total_events"] == 1
