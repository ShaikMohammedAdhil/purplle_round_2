import argparse
import json
import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.config import PipelineConfig
from pipeline.detect import PersonDetectionPipeline, VideoProcessingError
from pipeline.emit import EventGenerationError, generate_events
from pipeline.ingestion_client import EventUploadError, IngestionClient
from pipeline.tracker import TrackingError, load_detections, track_detections


def build_parser() -> argparse.ArgumentParser:
    """Build the detection pipeline command-line parser."""
    parser = argparse.ArgumentParser(description="Run Store Intelligence detection pipeline.")
    parser.add_argument("--video", required=True, help="Path to an mp4, avi, or mov video.")
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
        help="Directory where detections.jsonl will be written.",
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
        help="Camera ID to include in generated business events.",
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


def main(argv: list[str] | None = None) -> int:
    """Run the detection pipeline from command-line arguments."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)

    config = PipelineConfig(
        model_path=args.model,
        confidence_threshold=args.confidence,
        video_source=args.video,
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
    pipeline = PersonDetectionPipeline(config)
    ingestion_client = IngestionClient.from_config(config)
    started_at = time.perf_counter()

    try:
        summary = pipeline.run(Path(args.video))
        detections = load_detections(summary.output_file)
        tracked = track_detections(detections, config)
        _, event_summary = generate_events(tracked, config)
        upload_summary = ingestion_client.upload_events_file(event_summary.output_file)
        processing_time_ms = int((time.perf_counter() - started_at) * 1000)
    except (VideoProcessingError, TrackingError, EventGenerationError, EventUploadError) as exc:
        processing_time_ms = int((time.perf_counter() - started_at) * 1000)
        logging.error(
            json.dumps(
                {
                    "video": args.video,
                    "tracks": 0,
                    "visitors": 0,
                    "events": 0,
                    "uploaded": 0,
                    "failed": 0,
                    "processing_time_ms": processing_time_ms,
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

    print(f"Video: {summary.video_path}")
    print(
        "Metadata: "
        f"fps={summary.metadata.fps:.2f}, "
        f"frames={summary.metadata.frame_count}, "
        f"resolution={summary.metadata.width}x{summary.metadata.height}, "
        f"duration_ms={summary.metadata.duration_ms}"
    )
    print(f"Frame count processed: {summary.processed_frames}")
    print(f"Detection count: {summary.detection_count}")
    print(f"Tracks created: {len({item.track_id for item in tracked})}")
    print(f"Visitors created: {event_summary.visitors_created}")
    print(f"Events generated: {event_summary.events_generated}")
    print(f"Events uploaded: {upload_summary.uploaded_count}")
    print(f"Events failed: {upload_summary.failed_count}")
    print(f"Detections output file: {summary.output_file}")
    print(f"Events output file: {event_summary.output_file}")
    logging.info(
        json.dumps(
            {
                "video": args.video,
                "tracks": len({item.track_id for item in tracked}),
                "visitors": event_summary.visitors_created,
                "events": event_summary.events_generated,
                "uploaded": upload_summary.uploaded_count,
                "failed": upload_summary.failed_count,
                "processing_time_ms": processing_time_ms,
                "errors": [],
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
