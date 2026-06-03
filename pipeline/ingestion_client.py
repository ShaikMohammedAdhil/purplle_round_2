import json
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pipeline.config import PipelineConfig, resolve_project_path

logger = logging.getLogger(__name__)

INGEST_PATH = "/events/ingest"


class EventUploadError(RuntimeError):
    """Raised when a batch cannot be uploaded to the ingestion API."""


@dataclass(frozen=True)
class BatchUploadResult:
    """Result returned after uploading one batch of events."""

    batch_size: int
    inserted_count: int
    duplicate_count: int
    failed_count: int
    status_code: int
    attempts: int
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class EventUploadSummary:
    """Aggregate upload summary across all event batches."""

    attempted_count: int
    uploaded_count: int
    duplicate_count: int
    failed_count: int
    batch_count: int
    failed_batches: int


PostFunction = Callable[[str, list[dict[str, Any]], float], tuple[int, dict[str, Any]]]


class IngestionClient:
    """Uploads generated business events to the Store Intelligence ingestion API."""

    def __init__(
        self,
        *,
        api_base_url: str,
        batch_size: int,
        timeout: float,
        max_retries: int,
        post_function: PostFunction | None = None,
    ) -> None:
        """Initialize a reusable API client for event ingestion."""
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than zero")
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        if max_retries < 0:
            raise ValueError("max_retries cannot be negative")

        self.api_base_url = api_base_url.rstrip("/")
        self.batch_size = batch_size
        self.timeout = timeout
        self.max_retries = max_retries
        self._post_function = post_function or self._post_json

    @classmethod
    def from_config(
        cls,
        config: PipelineConfig,
        post_function: PostFunction | None = None,
    ) -> "IngestionClient":
        """Create an ingestion client from pipeline configuration."""
        return cls(
            api_base_url=config.api_base_url,
            batch_size=config.api_batch_size,
            timeout=config.api_timeout,
            max_retries=config.max_retries,
            post_function=post_function,
        )

    def upload_events_file(self, events_file: str | Path) -> EventUploadSummary:
        """Load generated JSONL events from disk and upload them in batches."""
        events = load_events(events_file)
        return self.upload_events(events)

    def upload_events(self, events: list[dict[str, Any]]) -> EventUploadSummary:
        """Upload events to the ingestion API and return aggregate counts."""
        started_at = time.perf_counter()
        batches = list(chunked(events, self.batch_size))
        batch_results = [self._upload_batch(batch) for batch in batches]

        summary = EventUploadSummary(
            attempted_count=len(events),
            uploaded_count=sum(result.inserted_count for result in batch_results),
            duplicate_count=sum(result.duplicate_count for result in batch_results),
            failed_count=sum(result.failed_count for result in batch_results),
            batch_count=len(batch_results),
            failed_batches=sum(
                1
                for result in batch_results
                if result.status_code == 0 or result.status_code >= 400
            ),
        )
        processing_time_ms = int((time.perf_counter() - started_at) * 1000)
        logger.info(
            json.dumps(
                {
                    "endpoint": INGEST_PATH,
                    "api_base_url": self.api_base_url,
                    "attempted_count": summary.attempted_count,
                    "uploaded_count": summary.uploaded_count,
                    "duplicate_count": summary.duplicate_count,
                    "failed_count": summary.failed_count,
                    "batch_count": summary.batch_count,
                    "failed_batches": summary.failed_batches,
                    "processing_time_ms": processing_time_ms,
                    "errors": [
                        error
                        for result in batch_results
                        for error in result.errors
                    ],
                }
            )
        )
        return summary

    def _upload_batch(self, batch: list[dict[str, Any]]) -> BatchUploadResult:
        """Upload one batch with retry handling."""
        endpoint = f"{self.api_base_url}{INGEST_PATH}"
        attempts_allowed = self.max_retries + 1
        errors: list[str] = []

        for attempt in range(1, attempts_allowed + 1):
            try:
                status_code, payload = self._post_function(endpoint, batch, self.timeout)
                if status_code >= 500 and attempt < attempts_allowed:
                    errors.append(f"HTTP {status_code}: {payload}")
                    continue
                if status_code >= 400:
                    return BatchUploadResult(
                        batch_size=len(batch),
                        inserted_count=0,
                        duplicate_count=0,
                        failed_count=len(batch),
                        status_code=status_code,
                        attempts=attempt,
                        errors=[f"HTTP {status_code}: {payload}"],
                    )
                return BatchUploadResult(
                    batch_size=len(batch),
                    inserted_count=int(payload.get("inserted_count", 0)),
                    duplicate_count=int(payload.get("duplicate_count", 0)),
                    failed_count=int(payload.get("failed_count", 0)),
                    status_code=status_code,
                    attempts=attempt,
                    errors=errors,
                )
            except (EventUploadError, TimeoutError, OSError, URLError) as exc:
                errors.append(f"{exc.__class__.__name__}: {exc}")
                if attempt >= attempts_allowed:
                    return BatchUploadResult(
                        batch_size=len(batch),
                        inserted_count=0,
                        duplicate_count=0,
                        failed_count=len(batch),
                        status_code=0,
                        attempts=attempt,
                        errors=errors,
                    )

        return BatchUploadResult(
            batch_size=len(batch),
            inserted_count=0,
            duplicate_count=0,
            failed_count=len(batch),
            status_code=0,
            attempts=attempts_allowed,
            errors=errors,
        )

    @staticmethod
    def _post_json(
        endpoint: str,
        batch: list[dict[str, Any]],
        timeout: float,
    ) -> tuple[int, dict[str, Any]]:
        """POST a JSON event batch using the Python standard library."""
        request = Request(
            endpoint,
            data=json.dumps(batch).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
                payload = json.loads(body) if body else {}
                return int(response.status), payload
        except HTTPError as exc:
            body = exc.read().decode("utf-8")
            payload = json.loads(body) if body else {"message": exc.reason}
            return int(exc.code), payload
        except (json.JSONDecodeError, OSError, URLError, TimeoutError) as exc:
            raise EventUploadError(f"API request failed: {exc}") from exc


def load_events(events_file: str | Path) -> list[dict[str, Any]]:
    """Load generated business events from a JSONL file."""
    source = resolve_project_path(events_file)
    if not source.exists():
        raise EventUploadError(f"Events file does not exist: {source}")

    events: list[dict[str, Any]] = []
    try:
        for line in source.read_text(encoding="utf-8").splitlines():
            if line.strip():
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise EventUploadError("Event JSONL rows must be objects")
                events.append(payload)
    except (OSError, json.JSONDecodeError) as exc:
        raise EventUploadError(f"Unable to load events JSONL: {exc}") from exc

    return events


def chunked(
    items: list[dict[str, Any]],
    size: int,
) -> Iterable[list[dict[str, Any]]]:
    """Yield fixed-size event batches."""
    for index in range(0, len(items), size):
        yield items[index : index + size]
