import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path

from pipeline.config import PipelineConfig, resolve_project_path
from pipeline.detect import DetectionRecord

logger = logging.getLogger(__name__)


class TrackingError(RuntimeError):
    """Raised when tracking cannot be completed."""


@dataclass(frozen=True)
class TrackedDetection:
    """Detection enriched with stable track identity."""

    track_id: int
    frame: int
    timestamp_ms: int
    bbox: list[float]
    confidence: float

    def to_json(self) -> str:
        """Serialize tracked detection as JSON."""
        return json.dumps(
            {
                "track_id": self.track_id,
                "frame": self.frame,
                "timestamp_ms": self.timestamp_ms,
                "bbox": self.bbox,
                "confidence": self.confidence,
            }
        )


@dataclass
class TrackState:
    """Mutable state for a tracked visitor candidate."""

    track_id: int
    first_seen: int
    last_seen: int
    frame_count: int
    last_bbox: list[float]
    confidence: float
    missing_frames: int = 0

    @property
    def centroid(self) -> tuple[float, float]:
        """Return the center point of the last bounding box."""
        x1, y1, x2, y2 = self.last_bbox
        return ((x1 + x2) / 2, (y1 + y2) / 2)


class CentroidTracker:
    """Simple centroid-based multi-object tracker for person detections."""

    def __init__(
        self,
        *,
        max_distance: float = 80.0,
        max_missing_frames: int = 15,
    ) -> None:
        """Initialize the tracker with matching and expiry thresholds."""
        self.max_distance = max_distance
        self.max_missing_frames = max_missing_frames
        self.next_track_id = 1
        self.tracks: dict[int, TrackState] = {}

    def update(self, detections: list[DetectionRecord]) -> list[TrackedDetection]:
        """Update tracker state for one frame of detections."""
        if not detections:
            self._mark_missing(unmatched_track_ids=set(self.tracks))
            return []

        matched_tracks: set[int] = set()
        matched_detections: set[int] = set()
        assignments = self._match_detections(detections)
        tracked: list[TrackedDetection] = []

        for track_id, detection_index in assignments:
            detection = detections[detection_index]
            state = self.tracks[track_id]
            state.last_seen = detection.frame
            state.frame_count += 1
            state.last_bbox = detection.bbox
            state.confidence = detection.confidence
            state.missing_frames = 0
            matched_tracks.add(track_id)
            matched_detections.add(detection_index)
            tracked.append(self._tracked_detection(track_id, detection))

        for index, detection in enumerate(detections):
            if index in matched_detections:
                continue
            track_id = self._create_track(detection)
            matched_tracks.add(track_id)
            tracked.append(self._tracked_detection(track_id, detection))

        self._mark_missing(unmatched_track_ids=set(self.tracks) - matched_tracks)
        return sorted(tracked, key=lambda item: item.track_id)

    def _match_detections(
        self,
        detections: list[DetectionRecord],
    ) -> list[tuple[int, int]]:
        """Greedily match detections to existing tracks by centroid distance."""
        candidates: list[tuple[float, int, int]] = []
        for track_id, track in self.tracks.items():
            for index, detection in enumerate(detections):
                distance = self._distance(track.centroid, self._centroid(detection.bbox))
                if distance <= self.max_distance:
                    candidates.append((distance, track_id, index))

        assignments: list[tuple[int, int]] = []
        used_tracks: set[int] = set()
        used_detections: set[int] = set()
        for _, track_id, detection_index in sorted(candidates, key=lambda item: item[0]):
            if track_id in used_tracks or detection_index in used_detections:
                continue
            assignments.append((track_id, detection_index))
            used_tracks.add(track_id)
            used_detections.add(detection_index)
        return assignments

    def _create_track(self, detection: DetectionRecord) -> int:
        """Create a new track from an unmatched detection."""
        track_id = self.next_track_id
        self.next_track_id += 1
        self.tracks[track_id] = TrackState(
            track_id=track_id,
            first_seen=detection.frame,
            last_seen=detection.frame,
            frame_count=1,
            last_bbox=detection.bbox,
            confidence=detection.confidence,
        )
        return track_id

    def _mark_missing(self, *, unmatched_track_ids: set[int]) -> None:
        """Increment missing-frame counters and expire stale tracks."""
        expired: list[int] = []
        for track_id in unmatched_track_ids:
            state = self.tracks[track_id]
            state.missing_frames += 1
            if state.missing_frames > self.max_missing_frames:
                expired.append(track_id)

        for track_id in expired:
            del self.tracks[track_id]

    @staticmethod
    def _tracked_detection(track_id: int, detection: DetectionRecord) -> TrackedDetection:
        """Create a tracked detection from a track ID and raw detection."""
        return TrackedDetection(
            track_id=track_id,
            frame=detection.frame,
            timestamp_ms=detection.timestamp_ms,
            bbox=detection.bbox,
            confidence=detection.confidence,
        )

    @staticmethod
    def _centroid(bbox: list[float]) -> tuple[float, float]:
        """Return the center point of a bounding box."""
        x1, y1, x2, y2 = bbox
        return ((x1 + x2) / 2, (y1 + y2) / 2)

    @staticmethod
    def _distance(
        first: tuple[float, float],
        second: tuple[float, float],
    ) -> float:
        """Return Euclidean distance between two points."""
        return math.dist(first, second)


def load_detections(path: str | Path) -> list[DetectionRecord]:
    """Load detection records from a JSONL file."""
    source = Path(path)
    if not source.exists():
        raise TrackingError(f"Detections file does not exist: {source}")

    detections: list[DetectionRecord] = []
    try:
        for line in source.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            detections.append(
                DetectionRecord(
                    frame=int(payload["frame"]),
                    timestamp_ms=int(payload["timestamp_ms"]),
                    class_name=str(payload.get("class_name", "person")),
                    confidence=float(payload["confidence"]),
                    bbox=[float(value) for value in payload["bbox"]],
                )
            )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise TrackingError(f"Unable to load detections: {exc}") from exc

    return sorted(detections, key=lambda item: (item.frame, item.timestamp_ms))


def track_detections(
    detections: list[DetectionRecord],
    config: PipelineConfig,
) -> list[TrackedDetection]:
    """Track detections across frames with stable track IDs."""
    tracker = CentroidTracker(
        max_distance=config.tracker_max_distance,
        max_missing_frames=config.tracker_max_missing_frames,
    )
    tracked: list[TrackedDetection] = []

    frames = sorted({detection.frame for detection in detections})
    for frame in frames:
        frame_detections = [
            detection for detection in detections if detection.frame == frame
        ]
        tracked.extend(tracker.update(frame_detections))

    logger.info(
        json.dumps(
            {
                "input_detections": len(detections),
                "tracked_detections": len(tracked),
                "tracks": len({item.track_id for item in tracked}),
            }
        )
    )
    return tracked


def run_tracking(
    detections_file: str | Path,
    config: PipelineConfig,
) -> list[TrackedDetection]:
    """Load detection records from disk and return tracked detections."""
    detections = load_detections(resolve_project_path(detections_file))
    return track_detections(detections, config)
