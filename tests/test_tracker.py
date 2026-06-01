from pipeline.config import PipelineConfig
from pipeline.detect import DetectionRecord
from pipeline.tracker import CentroidTracker, track_detections


def detection(frame: int, bbox: list[float], confidence: float = 0.9) -> DetectionRecord:
    return DetectionRecord(
        frame=frame,
        timestamp_ms=frame * 100,
        class_name="person",
        confidence=confidence,
        bbox=bbox,
    )


def test_track_creation() -> None:
    tracker = CentroidTracker(max_distance=50)

    tracked = tracker.update([detection(1, [10, 10, 50, 50])])

    assert len(tracked) == 1
    assert tracked[0].track_id == 1
    assert tracker.tracks[1].first_seen == 1
    assert tracker.tracks[1].frame_count == 1


def test_stable_track_ids_across_frames() -> None:
    tracker = CentroidTracker(max_distance=80)

    first = tracker.update([detection(1, [10, 10, 50, 50])])
    second = tracker.update([detection(2, [15, 15, 55, 55])])

    assert first[0].track_id == second[0].track_id
    assert tracker.tracks[1].last_seen == 2
    assert tracker.tracks[1].frame_count == 2


def test_new_track_created_for_far_detection() -> None:
    tracker = CentroidTracker(max_distance=30)

    tracker.update([detection(1, [10, 10, 50, 50])])
    tracked = tracker.update([detection(2, [300, 300, 360, 360])])

    assert tracked[0].track_id == 2
    assert set(tracker.tracks) == {1, 2}


def test_track_detections_groups_by_frame() -> None:
    detections = [
        detection(1, [10, 10, 50, 50]),
        detection(2, [14, 14, 54, 54]),
        detection(2, [300, 300, 340, 340]),
    ]

    tracked = track_detections(detections, PipelineConfig())

    assert len(tracked) == 3
    assert {item.track_id for item in tracked} == {1, 2}
