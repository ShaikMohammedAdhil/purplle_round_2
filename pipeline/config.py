from dataclasses import dataclass
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class PipelineConfig:
    """Runtime configuration for the detection pipeline."""

    model_path: str = os.getenv("PIPELINE_MODEL_PATH", "yolov8n.pt")
    confidence_threshold: float = float(os.getenv("PIPELINE_CONFIDENCE_THRESHOLD", "0.5"))
    video_source: str = os.getenv("PIPELINE_VIDEO_SOURCE", "data/input.mp4")
    output_directory: str = os.getenv("PIPELINE_OUTPUT_DIRECTORY", "data/pipeline_outputs")
    frame_skip: int = int(os.getenv("PIPELINE_FRAME_SKIP", "1"))
    api_base_url: str = os.getenv("PIPELINE_API_BASE_URL", "http://127.0.0.1:8000")
    api_timeout: float = float(os.getenv("PIPELINE_API_TIMEOUT", "10.0"))
    api_batch_size: int = int(os.getenv("PIPELINE_API_BATCH_SIZE", "100"))
    max_retries: int = int(os.getenv("PIPELINE_MAX_RETRIES", "3"))
    store_id: str = os.getenv("PIPELINE_STORE_ID", "STORE_001")
    camera_id: str = os.getenv("PIPELINE_CAMERA_ID", "CAM_001")
    visitor_id_prefix: str = os.getenv("PIPELINE_VISITOR_ID_PREFIX", "")
    zones_path: str = os.getenv("PIPELINE_ZONES_PATH", "pipeline/zones.json")
    tracker_max_distance: float = float(os.getenv("PIPELINE_TRACKER_MAX_DISTANCE", "80.0"))
    tracker_max_missing_frames: int = int(
        os.getenv("PIPELINE_TRACKER_MAX_MISSING_FRAMES", "15")
    )
    zone_dwell_threshold_ms: int = int(
        os.getenv("PIPELINE_ZONE_DWELL_THRESHOLD_MS", "3000")
    )


MODEL_PATH = PipelineConfig.model_path
CONFIDENCE_THRESHOLD = PipelineConfig.confidence_threshold
VIDEO_SOURCE = PipelineConfig.video_source
OUTPUT_DIRECTORY = PipelineConfig.output_directory
FRAME_SKIP = PipelineConfig.frame_skip
API_BASE_URL = PipelineConfig.api_base_url
API_TIMEOUT = PipelineConfig.api_timeout
API_BATCH_SIZE = PipelineConfig.api_batch_size
MAX_RETRIES = PipelineConfig.max_retries
STORE_ID = PipelineConfig.store_id
CAMERA_ID = PipelineConfig.camera_id
VISITOR_ID_PREFIX = PipelineConfig.visitor_id_prefix
ZONES_PATH = PipelineConfig.zones_path
TRACKER_MAX_DISTANCE = PipelineConfig.tracker_max_distance
TRACKER_MAX_MISSING_FRAMES = PipelineConfig.tracker_max_missing_frames
ZONE_DWELL_THRESHOLD_MS = PipelineConfig.zone_dwell_threshold_ms


def default_config() -> PipelineConfig:
    """Return the default pipeline configuration."""
    return PipelineConfig()


def resolve_project_path(path: str | Path) -> Path:
    """Resolve relative paths from the project root while preserving absolute paths."""
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return PROJECT_ROOT / candidate
