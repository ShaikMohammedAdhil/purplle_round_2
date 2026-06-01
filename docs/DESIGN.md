# Design

## Goal

Store Intelligence turns CCTV-derived person movement into store operations analytics. The project is intentionally small: a FastAPI API, a local SQLAlchemy/SQLite store, a Python detection pipeline, and a Streamlit dashboard.

## Backend Design

The API is organized around thin route modules and service classes:

- Routes handle request parsing, dependency injection, status codes, and safe error responses.
- Services contain ingestion, metrics, session, funnel, anomaly, queue, and heatmap logic.
- SQLAlchemy models provide the persisted event and derived session tables.
- Pydantic schemas validate incoming event batches before persistence.

Events are the source of truth. Derived analytics either query events directly or use `visitor_sessions` after `POST /sessions/rebuild` replays the event stream.

## Pipeline Design

The pipeline runs in stages:

1. Load a video with OpenCV.
2. Run YOLO person detection on selected frames.
3. Track detections with a centroid tracker.
4. Convert track IDs into visitor IDs for the run.
5. Resolve each tracked detection into a configured store zone.
6. Emit ingestion-compatible business events.
7. Upload generated events to `POST /events/ingest`.

The generated metadata includes `track_id` and `frame` so API-side inspection can connect stored events back to pipeline output.

## Billing Queue Semantics

Entering `BILLING_ZONE` emits `BILLING_QUEUE_JOIN`. Leaving billing through `EXIT_ZONE` is treated as a successful checkout path and does not emit `BILLING_QUEUE_ABANDON`. Leaving billing for another zone emits `BILLING_QUEUE_ABANDON`.

## Dashboard Design

The Streamlit dashboard is a lightweight operations surface. It reads from the API and shows metrics, funnel status, queue status, anomalies, and heatmap data for a selected store and optional time window.

## Deployment Design

The default deployment remains local and Docker-friendly. Configuration is environment-based where useful, with safe local defaults. Production concerns such as authentication, migrations, managed databases, and CI/CD remain outside this focused challenge implementation.
