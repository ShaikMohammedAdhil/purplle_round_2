import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from pipeline.config import PipelineConfig, resolve_project_path
from pipeline.tracker import TrackedDetection

logger = logging.getLogger(__name__)


class EventGenerationError(RuntimeError):
    """Raised when tracked detections cannot be converted into events."""


@dataclass(frozen=True)
class Zone:
    """Rectangular store zone."""

    zone_id: str
    x1: float
    y1: float
    x2: float
    y2: float

    def contains_point(self, x: float, y: float) -> bool:
        """Return true if a point is inside the zone rectangle."""
        return self.x1 <= x <= self.x2 and self.y1 <= y <= self.y2


@dataclass(frozen=True)
class BusinessEvent:
    """Business event compatible with the ingestion API."""

    event_id: str
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: str
    timestamp: str
    zone_id: str | None
    dwell_ms: int
    is_staff: bool
    confidence: float
    metadata: dict[str, int | str]

    def to_dict(self) -> dict[str, object]:
        """Convert the event into a JSON-serializable dictionary."""
        return {
            "event_id": self.event_id,
            "store_id": self.store_id,
            "camera_id": self.camera_id,
            "visitor_id": self.visitor_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "zone_id": self.zone_id,
            "dwell_ms": self.dwell_ms,
            "is_staff": self.is_staff,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }

    def to_json(self) -> str:
        """Serialize the event as one JSON line."""
        return json.dumps(self.to_dict())


@dataclass
class VisitorPipelineState:
    """Mutable state used to derive events for one visitor."""

    visitor_id: str
    current_zone: str | None = None
    previous_zone: str | None = None
    zone_enter_time_ms: int | None = None
    dwell_emitted_for_zone: bool = False
    exited: bool = False
    billing_joined: bool = False
    billing_converted: bool = False
    seen_entry: bool = False


@dataclass(frozen=True)
class EventGenerationSummary:
    """Summary of event generation output."""

    visitors_created: int
    events_generated: int
    output_file: str


class ZoneMap:
    """Loads and resolves rectangular store zones."""

    def __init__(self, zones: list[Zone]) -> None:
        """Initialize a zone resolver."""
        if not zones:
            raise EventGenerationError("Zone configuration must contain at least one zone")
        self.zones = zones

    @classmethod
    def from_file(cls, path: str | Path) -> "ZoneMap":
        """Load zones from a JSON file."""
        source = resolve_project_path(path)
        if not source.exists():
            raise EventGenerationError(f"Zones file does not exist: {source}")

        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
            zones_payload = payload["zones"]
            zones = [
                Zone(
                    zone_id=str(item["zone_id"]),
                    x1=float(item["x1"]),
                    y1=float(item["y1"]),
                    x2=float(item["x2"]),
                    y2=float(item["y2"]),
                )
                for item in zones_payload
            ]
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise EventGenerationError(f"Invalid zone configuration: {exc}") from exc

        for zone in zones:
            if zone.x2 <= zone.x1 or zone.y2 <= zone.y1:
                raise EventGenerationError(f"Invalid rectangle for zone {zone.zone_id}")

        return cls(zones)

    def locate(self, bbox: list[float]) -> str | None:
        """Return the first zone containing the bbox center point."""
        x1, y1, x2, y2 = bbox
        center_x = (x1 + x2) / 2
        center_y = (y1 + y2) / 2
        for zone in self.zones:
            if zone.contains_point(center_x, center_y):
                return zone.zone_id
        return None


class VisitorIdRegistry:
    """Maintains track ID to visitor ID mapping for one pipeline execution."""

    def __init__(self) -> None:
        """Initialize an empty visitor registry."""
        self._mapping: dict[int, str] = {}

    def visitor_id_for(self, track_id: int) -> str:
        """Return a stable visitor ID for the provided track ID."""
        if track_id not in self._mapping:
            self._mapping[track_id] = f"VISITOR_{len(self._mapping) + 1:06d}"
        return self._mapping[track_id]

    @property
    def count(self) -> int:
        """Return the number of visitor IDs created."""
        return len(self._mapping)


class BusinessEventGenerator:
    """Converts tracked detections and zone transitions into business events."""

    def __init__(
        self,
        *,
        config: PipelineConfig,
        zone_map: ZoneMap,
        base_time: datetime | None = None,
    ) -> None:
        """Initialize the event generator."""
        self.config = config
        self.zone_map = zone_map
        self.base_time = base_time or datetime.now(timezone.utc)
        self.visitors = VisitorIdRegistry()
        self.states: dict[int, VisitorPipelineState] = {}

    def generate(self, tracked: list[TrackedDetection]) -> list[BusinessEvent]:
        """Generate business events from tracked detections."""
        events: list[BusinessEvent] = []
        for detection in sorted(tracked, key=lambda item: (item.frame, item.track_id)):
            visitor_id = self.visitors.visitor_id_for(detection.track_id)
            state = self.states.setdefault(
                detection.track_id,
                VisitorPipelineState(visitor_id=visitor_id),
            )
            zone_id = self.zone_map.locate(detection.bbox)
            events.extend(self._process_detection(detection, state, zone_id))

        logger.info(
            json.dumps(
                {
                    "tracks": len({item.track_id for item in tracked}),
                    "visitors": self.visitors.count,
                    "events": len(events),
                }
            )
        )
        return events

    def write_events(
        self,
        events: list[BusinessEvent],
        output_directory: str | Path | None = None,
    ) -> Path:
        """Write events to events.jsonl and return the output path."""
        directory = resolve_project_path(output_directory or self.config.output_directory)
        output_file = directory / "events.jsonl"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with output_file.open("w", encoding="utf-8") as handle:
                for event in events:
                    handle.write(event.to_json() + "\n")
        except OSError as exc:
            raise EventGenerationError(f"Unable to write events JSONL: {exc}") from exc
        return output_file

    def _process_detection(
        self,
        detection: TrackedDetection,
        state: VisitorPipelineState,
        zone_id: str | None,
    ) -> list[BusinessEvent]:
        """Apply one tracked detection to visitor state and return emitted events."""
        events: list[BusinessEvent] = []
        if zone_id == state.current_zone:
            events.extend(self._maybe_emit_dwell(detection, state, zone_id))
            return events

        if state.current_zone is not None:
            dwell_ms = self._zone_dwell_ms(detection.timestamp_ms, state)
            events.append(
                self._event(
                    detection=detection,
                    state=state,
                    event_type="ZONE_EXIT",
                    zone_id=state.current_zone,
                    dwell_ms=dwell_ms,
                )
            )
            if state.current_zone == "BILLING_ZONE" and zone_id == "EXIT_ZONE":
                state.billing_converted = True
            elif (
                state.current_zone == "BILLING_ZONE"
                and state.billing_joined
                and not state.billing_converted
            ):
                events.append(
                    self._event(
                        detection=detection,
                        state=state,
                        event_type="BILLING_QUEUE_ABANDON",
                        zone_id=state.current_zone,
                        dwell_ms=dwell_ms,
                    )
                )

        state.previous_zone = state.current_zone
        state.current_zone = zone_id
        state.zone_enter_time_ms = detection.timestamp_ms if zone_id is not None else None
        state.dwell_emitted_for_zone = False

        if zone_id is None:
            return events

        if zone_id == "ENTRY_ZONE":
            if state.exited:
                events.append(
                    self._event(
                        detection=detection,
                        state=state,
                        event_type="REENTRY",
                        zone_id=zone_id,
                    )
                )
                state.exited = False
            elif not state.seen_entry:
                events.append(
                    self._event(
                        detection=detection,
                        state=state,
                        event_type="ENTRY",
                        zone_id=zone_id,
                    )
                )
                state.seen_entry = True

        events.append(
            self._event(
                detection=detection,
                state=state,
                event_type="ZONE_ENTER",
                zone_id=zone_id,
            )
        )

        if zone_id == "BILLING_ZONE":
            state.billing_joined = True
            state.billing_converted = False
            events.append(
                self._event(
                    detection=detection,
                    state=state,
                    event_type="BILLING_QUEUE_JOIN",
                    zone_id=zone_id,
                )
            )

        if zone_id == "EXIT_ZONE":
            state.exited = True
            events.append(
                self._event(
                    detection=detection,
                    state=state,
                    event_type="EXIT",
                    zone_id=zone_id,
                )
            )

        return events

    def _maybe_emit_dwell(
        self,
        detection: TrackedDetection,
        state: VisitorPipelineState,
        zone_id: str | None,
    ) -> list[BusinessEvent]:
        """Emit a ZONE_DWELL event once the dwell threshold is crossed."""
        if zone_id is None or state.dwell_emitted_for_zone:
            return []
        dwell_ms = self._zone_dwell_ms(detection.timestamp_ms, state)
        if dwell_ms < self.config.zone_dwell_threshold_ms:
            return []
        state.dwell_emitted_for_zone = True
        return [
            self._event(
                detection=detection,
                state=state,
                event_type="ZONE_DWELL",
                zone_id=zone_id,
                dwell_ms=dwell_ms,
            )
        ]

    def _event(
        self,
        *,
        detection: TrackedDetection,
        state: VisitorPipelineState,
        event_type: str,
        zone_id: str | None,
        dwell_ms: int = 0,
    ) -> BusinessEvent:
        """Build one ingestion-compatible business event."""
        timestamp = self.base_time + timedelta(milliseconds=detection.timestamp_ms)
        return BusinessEvent(
            event_id=f"evt_{uuid4().hex}",
            store_id=self.config.store_id,
            camera_id=self.config.camera_id,
            visitor_id=state.visitor_id,
            event_type=event_type,
            timestamp=timestamp.isoformat().replace("+00:00", "Z"),
            zone_id=zone_id,
            dwell_ms=max(dwell_ms, 0),
            is_staff=False,
            confidence=detection.confidence,
            metadata={
                "track_id": detection.track_id,
                "frame": detection.frame,
            },
        )

    @staticmethod
    def _zone_dwell_ms(
        timestamp_ms: int,
        state: VisitorPipelineState,
    ) -> int:
        """Return elapsed milliseconds since the visitor entered the current zone."""
        if state.zone_enter_time_ms is None:
            return 0
        return max(timestamp_ms - state.zone_enter_time_ms, 0)


def generate_events(
    tracked: list[TrackedDetection],
    config: PipelineConfig,
    zone_map: ZoneMap | None = None,
) -> tuple[list[BusinessEvent], EventGenerationSummary]:
    """Generate events from tracked detections and persist them to JSONL."""
    zones = zone_map or ZoneMap.from_file(config.zones_path)
    generator = BusinessEventGenerator(config=config, zone_map=zones)
    events = generator.generate(tracked)
    output_file = generator.write_events(events)
    return events, EventGenerationSummary(
        visitors_created=generator.visitors.count,
        events_generated=len(events),
        output_file=str(output_file),
    )
