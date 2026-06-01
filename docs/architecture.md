# Architecture

## System Overview

Store Intelligence API is a FastAPI backend for retail analytics generated from CCTV event streams.

The system stores normalized event data in SQLite and exposes analytics APIs for ingestion, store metrics, sessions, funnels, anomalies, and queue monitoring.

The backend follows a service-layer design:

- Routers handle HTTP concerns.
- Services contain business logic.
- SQLAlchemy ORM handles persistence.
- Pydantic models define request and response schemas.
- FastAPI dependency injection provides database sessions.

## Data Flow

1. CCTV analytics emits structured events.
2. `POST /events/ingest` validates and stores events.
3. Metrics APIs aggregate event data.
4. Session rebuild replays events into visitor sessions.
5. Funnel APIs calculate customer journey progression.
6. Anomaly APIs combine events, sessions, and funnel analytics to detect operational issues.

## Event Lifecycle

Events are created outside the API by CCTV analytics and posted to the ingestion endpoint.

The ingestion layer:

- Validates payloads with Pydantic.
- Enforces event type constraints.
- Deduplicates by `event_id`.
- Stores event metadata as JSON text.
- Returns partial success when some events fail.

The `Event` model is the source of truth for analytics calculations.

## Session Lifecycle

Visitor sessions are derived from events using `SessionService`.

Session rules:

- `ENTRY` creates a session when no active session exists.
- `EXIT` closes the active session and records total dwell time.
- `ZONE_DWELL` accumulates dwell milliseconds.
- `BILLING_QUEUE_JOIN` marks conversion and increments purchase count.
- A new `ENTRY` after an ended session is treated as re-entry internally.

Historical sessions can be reconstructed with `POST /sessions/rebuild`.

## Analytics Pipeline

`MetricsService` calculates store metrics directly from the `events` table:

- Unique visitors
- Entries and exits
- Staff entries
- Average dwell time
- Peak hour
- Queue joins
- Queue abandons
- Abandonment rate
- Conversion rate

`SessionService` calculates funnel metrics from event and session data:

- Entered visitors
- Engaged visitors
- Queue visitors
- Converted visitors
- Engagement rate
- Queue rate
- Conversion rate

All analytics support store filtering and optional time filtering where applicable.

## Anomaly Detection Pipeline

`AnomalyService` evaluates operational health from event, session, and funnel data.

Implemented anomalies:

- `LONG_DWELL_TIME`: average session dwell time is greater than 30 minutes.
- `QUEUE_CONGESTION`: queue joins are greater than 50 and abandonment rate is greater than 20 percent.
- `TRAFFIC_SPIKE`: current visitor count is greater than 2x historical average.
- `LOW_CONVERSION`: conversion rate is less than 5 percent.
- `EMPTY_STORE`: zero visitors are detected in the requested period.

Queue status is calculated separately:

- `NORMAL`: abandonment rate is less than 10 percent.
- `BUSY`: abandonment rate is at least 10 percent and less than 20 percent.
- `CONGESTED`: abandonment rate is at least 20 percent.

## Error Handling

Production-facing endpoints use explicit error handling for unexpected failures.

The API returns safe error responses without exposing stack traces or SQL details:

```json
{
  "success": false,
  "message": "Internal server error"
}
```

Structured logs include request IDs, endpoint names, store IDs, processing time, and error metadata.
