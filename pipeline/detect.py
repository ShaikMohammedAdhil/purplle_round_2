import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pipeline.config import PipelineConfig, resolve_project_path

logger = logging.getLogger(__name__)

SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov"}
PERSON_CLASS_ID = 0


class VideoProcessingError(RuntimeError):
    """Raised when the detection pipeline cannot process a video."""


class YoloModelProtocol(Protocol):
    """Protocol for YOLO-compatible model callables."""

    def __call__(self, frame: Any, verbose: bool = False) -> list[Any]:
        """Run detection for a frame and return model results."""


class LazyCv2Proxy:
    """Lazy OpenCV proxy that remains monkeypatchable in tests."""

    CAP_PROP_POS_FRAMES = 1
    CAP_PROP_FRAME_WIDTH = 3
    CAP_PROP_FRAME_HEIGHT = 4
    CAP_PROP_FPS = 5
    CAP_PROP_FRAME_COUNT = 7

    def VideoCapture(self, *args: Any, **kwargs: Any) -> Any:
        """Create an OpenCV video capture object."""
        return self._module().VideoCapture(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        """Load OpenCV on first access to non-patched attributes."""
        return getattr(self._module(), name)

    @staticmethod
    def _module() -> Any:
        """Import and return the real OpenCV module."""
        try:
            import cv2 as cv2_module
        except ImportError as exc:
            raise VideoProcessingError(
                "OpenCV is required to run video detection. Install opencv-python."
            ) from exc
        return cv2_module


cv2 = LazyCv2Proxy()


def _load_yolo_class() -> Any:
    """Import YOLO only when model inference is executed."""
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise VideoProcessingError(
            "Ultralytics is required to run YOLO detection. Install ultralytics."
        ) from exc
    return YOLO


@dataclass(frozen=True)
class VideoMetadata:
    """Basic metadata extracted from a video source."""

    fps: float
    frame_count: int
    width: int
    height: int
    duration_ms: int

    @property
    def resolution(self) -> tuple[int, int]:
        """Return video resolution as width and height."""
        return (self.width, self.height)


@dataclass(frozen=True)
class DetectionRecord:
    """Structured person detection record emitted by the pipeline."""

    frame: int
    timestamp_ms: int
    class_name: str
    confidence: float
    bbox: list[float]

    def to_json(self) -> str:
        """Serialize the detection record as one JSON line."""
        return json.dumps(
            {
                "frame": self.frame,
                "timestamp_ms": self.timestamp_ms,
                "class_name": self.class_name,
                "confidence": self.confidence,
                "bbox": self.bbox,
            }
        )


@dataclass(frozen=True)
class DetectionRunSummary:
    """Summary of one detection pipeline execution."""

    video_path: str
    metadata: VideoMetadata
    processed_frames: int
    detection_count: int
    output_file: str
    processing_time_ms: int


class PersonDetectionPipeline:
    """Extracts frames from video and writes YOLO person detections to JSONL."""

    def __init__(
        self,
        config: PipelineConfig,
        model: YoloModelProtocol | None = None,
    ) -> None:
        """Initialize the pipeline with runtime configuration and optional model."""
        self.config = config
        self.model = model

    def run(self, video_path: str | Path | None = None) -> DetectionRunSummary:
        """Run person detection for a video source and write JSONL output."""
        started_at = time.perf_counter()
        source = resolve_project_path(video_path or self.config.video_source)
        self._validate_video_path(source)

        output_directory = resolve_project_path(self.config.output_directory)
        output_file = output_directory / "detections.jsonl"

        try:
            output_directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise VideoProcessingError(f"Unable to create output directory: {exc}") from exc

        capture = cv2.VideoCapture(str(source))
        if not capture.isOpened():
            capture.release()
            raise VideoProcessingError(f"Unable to open video source: {source}")

        metadata = self._read_metadata(capture, cv2)
        model = self._load_model()
        processed_frames = 0
        detection_count = 0

        try:
            with output_file.open("w", encoding="utf-8") as handle:
                while True:
                    ok, frame = capture.read()
                    if not ok:
                        break

                    frame_number = int(capture.get(cv2.CAP_PROP_POS_FRAMES))
                    if not self._should_process_frame(frame_number):
                        continue

                    processed_frames += 1
                    timestamp_ms = self._timestamp_for_frame(
                        frame_number=frame_number,
                        fps=metadata.fps,
                    )
                    detections = self.detect_people(
                        model=model,
                        frame=frame,
                        frame_number=frame_number,
                        timestamp_ms=timestamp_ms,
                    )
                    for detection in detections:
                        handle.write(detection.to_json() + "\n")
                    detection_count += len(detections)
        except OSError as exc:
            raise VideoProcessingError(f"Unable to write detection output: {exc}") from exc
        except Exception as exc:
            raise VideoProcessingError(f"Detection pipeline failed: {exc}") from exc
        finally:
            capture.release()

        processing_time_ms = int((time.perf_counter() - started_at) * 1000)
        self._log_summary(
            video_path=str(source),
            frame_count=metadata.frame_count,
            processed_frames=processed_frames,
            detection_count=detection_count,
            processing_time_ms=processing_time_ms,
        )

        return DetectionRunSummary(
            video_path=str(source),
            metadata=metadata,
            processed_frames=processed_frames,
            detection_count=detection_count,
            output_file=str(output_file),
            processing_time_ms=processing_time_ms,
        )

    def detect_people(
        self,
        *,
        model: YoloModelProtocol,
        frame: Any,
        frame_number: int,
        timestamp_ms: int,
    ) -> list[DetectionRecord]:
        """Run YOLO inference on one frame and return person detections only."""
        try:
            results = model(frame, verbose=False)
        except Exception as exc:
            raise VideoProcessingError(f"YOLO inference failed: {exc}") from exc

        detections: list[DetectionRecord] = []
        for result in results:
            boxes = getattr(result, "boxes", None)
            if boxes is None:
                continue

            for box in boxes:
                class_id = int(box.cls[0].item())
                confidence = float(box.conf[0].item())
                if class_id != PERSON_CLASS_ID:
                    continue
                if confidence < self.config.confidence_threshold:
                    continue

                bbox = [float(value) for value in box.xyxy[0].tolist()]
                detections.append(
                    DetectionRecord(
                        frame=frame_number,
                        timestamp_ms=timestamp_ms,
                        class_name="person",
                        confidence=round(confidence, 4),
                        bbox=bbox,
                    )
                )

        return detections

    def _load_model(self) -> YoloModelProtocol:
        """Load the YOLO model once per pipeline run unless injected for tests."""
        if self.model is not None:
            return self.model

        model_path = resolve_project_path(self.config.model_path)
        model_reference = str(model_path) if model_path.exists() else self.config.model_path
        try:
            self.model = _load_yolo_class()(model_reference)
        except Exception as exc:
            raise VideoProcessingError(f"Unable to load YOLO model: {exc}") from exc
        return self.model

    def _read_metadata(self, capture: Any, cv2_module: Any) -> VideoMetadata:
        """Extract video metadata from an opened OpenCV capture."""
        fps = float(capture.get(cv2_module.CAP_PROP_FPS) or 0.0)
        frame_count = int(capture.get(cv2_module.CAP_PROP_FRAME_COUNT) or 0)
        width = int(capture.get(cv2_module.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2_module.CAP_PROP_FRAME_HEIGHT) or 0)

        if frame_count <= 0 or width <= 0 or height <= 0:
            raise VideoProcessingError("Video metadata is invalid or unavailable")

        duration_ms = int((frame_count / fps) * 1000) if fps > 0 else 0
        return VideoMetadata(
            fps=fps,
            frame_count=frame_count,
            width=width,
            height=height,
            duration_ms=duration_ms,
        )

    def _validate_video_path(self, source: Path) -> None:
        """Validate that the configured video source exists and is supported."""
        if not source.exists():
            raise VideoProcessingError(f"Video file does not exist: {source}")
        if source.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
            supported = ", ".join(sorted(SUPPORTED_VIDEO_EXTENSIONS))
            raise VideoProcessingError(
                f"Unsupported video extension '{source.suffix}'. Supported: {supported}"
            )

    def _should_process_frame(self, frame_number: int) -> bool:
        """Return true when the frame should be processed based on frame_skip."""
        frame_skip = max(int(self.config.frame_skip), 1)
        return frame_number == 1 or (frame_number - 1) % frame_skip == 0

    @staticmethod
    def _timestamp_for_frame(*, frame_number: int, fps: float) -> int:
        """Estimate frame timestamp in milliseconds."""
        if fps <= 0:
            return 0
        return int(((frame_number - 1) / fps) * 1000)

    @staticmethod
    def _log_summary(
        *,
        video_path: str,
        frame_count: int,
        processed_frames: int,
        detection_count: int,
        processing_time_ms: int,
    ) -> None:
        """Emit structured logging for one detection run."""
        logger.info(
            json.dumps(
                {
                    "video_path": video_path,
                    "frame_count": frame_count,
                    "processed_frames": processed_frames,
                    "detection_count": detection_count,
                    "processing_time_ms": processing_time_ms,
                }
            )
        )


def run_detection(video_path: str | Path) -> DetectionRunSummary:
    """Run the default person detection pipeline for a video path."""
    pipeline = PersonDetectionPipeline(PipelineConfig(video_source=str(video_path)))
    return pipeline.run(video_path)
