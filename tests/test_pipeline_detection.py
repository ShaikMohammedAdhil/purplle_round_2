import json
from pathlib import Path
from typing import Any

import pytest

from pipeline.config import PipelineConfig, default_config
from pipeline.detect import (
    DetectionRecord,
    PersonDetectionPipeline,
    VideoProcessingError,
)


class FakeCapture:
    """Small OpenCV VideoCapture test double."""

    CAP_PROP_POS_FRAMES = 1

    def __init__(self, frames: list[Any]) -> None:
        self.frames = frames
        self.index = 0
        self.released = False

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
            detect.cv2.CAP_PROP_FPS: 30.0,
            detect.cv2.CAP_PROP_FRAME_COUNT: float(len(self.frames)),
            detect.cv2.CAP_PROP_FRAME_WIDTH: 640.0,
            detect.cv2.CAP_PROP_FRAME_HEIGHT: 480.0,
            detect.cv2.CAP_PROP_POS_FRAMES: float(self.index),
        }
        return values.get(prop, 0.0)

    def release(self) -> None:
        self.released = True


def test_configuration_loading() -> None:
    config = default_config()

    assert config.model_path == "yolov8n.pt"
    assert config.confidence_threshold == 0.5
    assert config.frame_skip == 1
    assert config.api_base_url == "http://127.0.0.1:8000"


def test_invalid_video_path(tmp_path: Path) -> None:
    config = PipelineConfig(output_directory=str(tmp_path))
    pipeline = PersonDetectionPipeline(config, model=lambda frame, verbose=False: [])

    with pytest.raises(VideoProcessingError, match="Video file does not exist"):
        pipeline.run(tmp_path / "missing.mp4")


def test_output_file_creation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"fake video")

    from pipeline import detect

    monkeypatch.setattr(
        detect.cv2,
        "VideoCapture",
        lambda path: FakeCapture(frames=["frame-1", "frame-2"]),
    )

    config = PipelineConfig(output_directory=str(tmp_path / "out"))
    pipeline = PersonDetectionPipeline(config, model=lambda frame, verbose=False: [])

    summary = pipeline.run(video_path)

    assert Path(summary.output_file).exists()
    assert Path(summary.output_file).name == "detections.jsonl"
    assert summary.processed_frames == 2


def test_detection_record_structure() -> None:
    record = DetectionRecord(
        frame=125,
        timestamp_ms=4170,
        class_name="person",
        confidence=0.91,
        bbox=[1.0, 2.0, 3.0, 4.0],
    )

    payload = json.loads(record.to_json())

    assert payload == {
        "frame": 125,
        "timestamp_ms": 4170,
        "class_name": "person",
        "confidence": 0.91,
        "bbox": [1.0, 2.0, 3.0, 4.0],
    }


def test_jsonl_writing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"fake video")

    from pipeline import detect

    monkeypatch.setattr(
        detect.cv2,
        "VideoCapture",
        lambda path: FakeCapture(frames=["frame-1"]),
    )

    config = PipelineConfig(output_directory=str(tmp_path / "out"))
    pipeline = PersonDetectionPipeline(config, model=lambda frame, verbose=False: [])
    monkeypatch.setattr(
        pipeline,
        "detect_people",
        lambda **kwargs: [
            DetectionRecord(
                frame=1,
                timestamp_ms=0,
                class_name="person",
                confidence=0.99,
                bbox=[10.0, 20.0, 30.0, 40.0],
            )
        ],
    )

    summary = pipeline.run(video_path)
    lines = Path(summary.output_file).read_text(encoding="utf-8").splitlines()

    assert summary.detection_count == 1
    assert len(lines) == 1
    assert json.loads(lines[0])["class_name"] == "person"
