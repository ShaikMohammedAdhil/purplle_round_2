import argparse
import json
import logging
import re
import sys
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.config import PipelineConfig, resolve_project_path
from pipeline.detect import (
    SUPPORTED_VIDEO_EXTENSIONS,
    PersonDetectionPipeline,
    VideoProcessingError,
)
from pipeline.emit import EventGenerationError, generate_events
from pipeline.ingestion_client import EventUploadError, IngestionClient
from pipeline.tracker import TrackingError, load_detections, track_detections


CAMERA_PATTERN = re.compile(r"cam(?:era)?[_\-\s]*(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class CameraPipelineSummary:
    """Summary for one camera video processed by the pipeline."""

    camera_id: str
    video_path: str
    output_dir: str
    tracks: int = 0
    visitors: int = 0
    events: int = 0
    uploaded: int = 0
    failed: int = 0
    processed_frames: int = 0
    detection_count: int = 0
    detections_output_file: str | None = None
    events_output_file: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    processing_time_ms: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)

    @property
    def success(self) -> bool:
        """Return true when the camera completed without pipeline errors."""
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable summary dictionary."""
        return asdict(self)


@dataclass(frozen=True)
class StorePipelineSummary:
    """Aggregated pipeline summary for a store run."""

    store_id: str
    cameras_processed: int
    cameras_failed: int
    total_tracks: int
    total_visitors: int
    total_events: int
    uploaded: int
    failed: int
    camera_summaries: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable summary dictionary."""
        return asdict(self)


def build_parser() -> argparse.ArgumentParser:
    """Build the detection pipeline command-line parser."""
    parser = argparse.ArgumentParser(description="Run Store Intelligence detection pipeline.")
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--video", help="Path to one mp4, avi, or mov video.")
    input_group.add_argument(
        "--video-dir",
        help="Directory containing mp4, avi, or mov videos to process as cameras.",
    )
    parser.add_argument(
        "--model",
        default=PipelineConfig.model_path,
        help="YOLO model path or model name.",
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=PipelineConfig.confidence_threshold,
        help="Minimum confidence threshold for person detections.",
    )
    parser.add_argument(
        "--output-dir",
        default=PipelineConfig.output_directory,
        help="Directory where pipeline output files will be written.",
    )
    parser.add_argument(
        "--frame-skip",
        type=int,
        default=PipelineConfig.frame_skip,
        help="Process every Nth frame.",
    )
    parser.add_argument(
        "--zones",
        default=PipelineConfig.zones_path,
        help="Path to store zone configuration JSON.",
    )
    parser.add_argument(
        "--store-id",
        default=PipelineConfig.store_id,
        help="Store ID to include in generated business events.",
    )
    parser.add_argument(
        "--camera-id",
        default=PipelineConfig.camera_id,
        help="Camera ID for single-video mode.",
    )
    parser.add_argument(
        "--api-url",
        default=PipelineConfig.api_base_url,
        help="Base URL for the Store Intelligence API.",
    )
    parser.add_argument(
        "--api-timeout",
        type=float,
        default=PipelineConfig.api_timeout,
        help="Timeout in seconds for ingestion API requests.",
    )
    parser.add_argument(
        "--api-batch-size",
        type=int,
        default=PipelineConfig.api_batch_size,
        help="Number of generated events to upload per ingestion request.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=PipelineConfig.max_retries,
        help="Maximum retry attempts after a failed ingestion request.",
    )
    return parser


def config_from_args(args: argparse.Namespace) -> PipelineConfig:
    """Build the base pipeline configuration from CLI arguments."""
    return PipelineConfig(
        model_path=args.model,
        confidence_threshold=args.confidence,
        video_source=args.video or args.video_dir or PipelineConfig.video_source,
        output_directory=args.output_dir,
        frame_skip=args.frame_skip,
        zones_path=args.zones,
        store_id=args.store_id,
        camera_id=args.camera_id,
        api_base_url=args.api_url,
        api_timeout=args.api_timeout,
        api_batch_size=args.api_batch_size,
        max_retries=args.max_retries,
    )


def discover_videos(video_dir: str | Path) -> list[Path]:
    """Return supported videos in a directory sorted by filename."""
    directory = resolve_project_path(video_dir)
    if not directory.exists():
        raise VideoProcessingError(f"Video directory does not exist: {directory}")
    if not directory.is_dir():
        raise VideoProcessingError(f"Video directory is not a directory: {directory}")

    videos = [
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS
    ]
    if not videos:
        supported = ", ".join(sorted(SUPPORTED_VIDEO_EXTENSIONS))
        raise VideoProcessingError(
            f"No supported videos found in {directory}. Supported: {supported}"
        )
    return sorted(videos, key=lambda path: path.name.lower())


def infer_camera_id(video_path: str | Path, sequence_number: int) -> str:
    """Infer a camera ID from a video filename or fallback sequence number."""
    stem = Path(video_path).stem
    match = CAMERA_PATTERN.search(stem)
    if match is not None:
        return f"CAM_{int(match.group(1))}"
    return f"CAM_{sequence_number}"


def run_camera(video_path: str | Path, config: PipelineConfig) -> CameraPipelineSummary:
    """Run detection, tracking, event generation, and upload for one camera."""
    started_at = time.perf_counter()
    resolved_video = resolve_project_path(video_path)
    output_dir = resolve_project_path(config.output_directory)

    try:
        pipeline = PersonDetectionPipeline(config)
        ingestion_client = IngestionClient.from_config(config)
        detection_summary = pipeline.run(resolved_video)
        detections = load_detections(detection_summary.output_file)
        tracked = track_detections(detections, config)
        _, event_summary = generate_events(tracked, config)
        upload_summary = ingestion_client.upload_events_file(event_summary.output_file)
    except (VideoProcessingError, TrackingError, EventGenerationError, EventUploadError) as exc:
        processing_time_ms = int((time.perf_counter() - started_at) * 1000)
        return CameraPipelineSummary(
            camera_id=config.camera_id,
            video_path=str(resolved_video),
            output_dir=str(output_dir),
            processing_time_ms=processing_time_ms,
            errors=[
                {
                    "type": exc.__class__.__name__,
                    "message": str(exc),
                }
            ],
        )

    processing_time_ms = int((time.perf_counter() - started_at) * 1000)
    return CameraPipelineSummary(
        camera_id=config.camera_id,
        video_path=detection_summary.video_path,
        output_dir=str(output_dir),
        tracks=len({item.track_id for item in tracked}),
        visitors=event_summary.visitors_created,
        events=event_summary.events_generated,
        uploaded=upload_summary.uploaded_count,
        failed=upload_summary.failed_count,
        processed_frames=detection_summary.processed_frames,
        detection_count=detection_summary.detection_count,
        detections_output_file=detection_summary.output_file,
        events_output_file=event_summary.output_file,
        metadata={
            "fps": detection_summary.metadata.fps,
            "frame_count": detection_summary.metadata.frame_count,
            "width": detection_summary.metadata.width,
            "height": detection_summary.metadata.height,
            "duration_ms": detection_summary.metadata.duration_ms,
        },
        processing_time_ms=processing_time_ms,
    )


def summarize_store_run(
    *,
    store_id: str,
    camera_summaries: list[CameraPipelineSummary],
) -> StorePipelineSummary:
    """Aggregate per-camera summaries into one store-level summary."""
    successful = [summary for summary in camera_summaries if summary.success]
    failed = [summary for summary in camera_summaries if not summary.success]
    return StorePipelineSummary(
        store_id=store_id,
        cameras_processed=len(successful),
        cameras_failed=len(failed),
        total_tracks=sum(summary.tracks for summary in successful),
        total_visitors=sum(summary.visitors for summary in successful),
        total_events=sum(summary.events for summary in successful),
        uploaded=sum(summary.uploaded for summary in successful),
        failed=sum(summary.failed for summary in successful),
        camera_summaries=[summary.to_dict() for summary in camera_summaries],
    )


def run_video_directory(
    *,
    video_dir: str | Path,
    base_config: PipelineConfig,
) -> StorePipelineSummary:
    """Process all supported videos in a directory as separate cameras."""
    videos = discover_videos(video_dir)
    output_root = resolve_project_path(base_config.output_directory)
    camera_summaries: list[CameraPipelineSummary] = []

    for index, video_path in enumerate(videos, start=1):
        camera_id = infer_camera_id(video_path, index)
        camera_output_dir = output_root / camera_id
        camera_config = replace(
            base_config,
            video_source=str(video_path),
            camera_id=camera_id,
            visitor_id_prefix=f"{camera_id}_",
            output_directory=str(camera_output_dir),
        )
        summary = run_camera(video_path, camera_config)
        camera_summaries.append(summary)
        if summary.errors:
            logging.error(
                json.dumps(
                    {
                        "camera_id": camera_id,
                        "video": str(video_path),
                        "errors": summary.errors,
                    }
                )
            )

    return summarize_store_run(
        store_id=base_config.store_id,
        camera_summaries=camera_summaries,
    )


def print_single_camera_summary(summary: CameraPipelineSummary) -> None:
    """Print the existing single-camera summary format."""
    print(f"Video: {summary.video_path}")
    metadata = summary.metadata
    print(
        "Metadata: "
        f"fps={float(metadata.get('fps', 0.0)):.2f}, "
        f"frames={int(metadata.get('frame_count', 0))}, "
        f"resolution={int(metadata.get('width', 0))}x{int(metadata.get('height', 0))}, "
        f"duration_ms={int(metadata.get('duration_ms', 0))}"
    )
    print(f"Frame count processed: {summary.processed_frames}")
    print(f"Detection count: {summary.detection_count}")
    print(f"Tracks created: {summary.tracks}")
    print(f"Visitors created: {summary.visitors}")
    print(f"Events generated: {summary.events}")
    print(f"Events uploaded: {summary.uploaded}")
    print(f"Events failed: {summary.failed}")
    print(f"Detections output file: {summary.detections_output_file}")
    print(f"Events output file: {summary.events_output_file}")


def main(argv: list[str] | None = None) -> int:
    """Run the detection pipeline from command-line arguments."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    base_config = config_from_args(args)

    if args.video_dir:
        try:
            store_summary = run_video_directory(
                video_dir=args.video_dir,
                base_config=base_config,
            )
        except VideoProcessingError as exc:
            logging.error(
                json.dumps(
                    {
                        "video_dir": args.video_dir,
                        "errors": [
                            {
                                "type": exc.__class__.__name__,
                                "message": str(exc),
                            }
                        ],
                    }
                )
            )
            return 1
        print(json.dumps(store_summary.to_dict(), indent=2))
        logging.info(json.dumps(store_summary.to_dict()))
        return 0 if store_summary.cameras_processed > 0 else 1

    single_config = replace(
        base_config,
        video_source=args.video,
        camera_id=args.camera_id,
    )
    summary = run_camera(args.video, single_config)
    if summary.errors:
        logging.error(
            json.dumps(
                {
                    "video": args.video,
                    "tracks": 0,
                    "visitors": 0,
                    "events": 0,
                    "uploaded": 0,
                    "failed": 0,
                    "processing_time_ms": summary.processing_time_ms,
                    "errors": summary.errors,
                }
            )
        )
        return 1

    print_single_camera_summary(summary)
    logging.info(
        json.dumps(
            {
                "video": args.video,
                "tracks": summary.tracks,
                "visitors": summary.visitors,
                "events": summary.events,
                "uploaded": summary.uploaded,
                "failed": summary.failed,
                "processing_time_ms": summary.processing_time_ms,
                "errors": [],
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
