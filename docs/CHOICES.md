# Choices

## FastAPI

FastAPI keeps the API compact while providing request validation, dependency injection, OpenAPI docs, and a straightforward test client.

## SQLAlchemy with SQLite

SQLAlchemy provides a clean ORM layer and portable aggregate queries. SQLite is sufficient for the challenge and local demos. The code keeps database access behind session dependencies so a later PostgreSQL migration would be contained.

## Event-First Analytics

The `events` table is the durable source of truth. Metrics and heatmaps query it directly. Sessions are derived by replaying events so funnel behavior can be rebuilt from historical data.

## Pydantic Event Schema

The ingestion schema validates required event fields, known event types, confidence bounds, dwell bounds, and metadata. Pipeline metadata fields such as `track_id` and `frame` are explicitly accepted so useful detection context is preserved.

## Simple Centroid Tracker

The pipeline uses a centroid tracker instead of introducing a heavier tracking dependency. This is enough for deterministic tests and a challenge-scale proof of event generation.

## YOLO and OpenCV

OpenCV handles video loading and frame extraction. Ultralytics YOLO is loaded lazily so API tests and non-detection tests do not require model execution.

## Streamlit Dashboard

Streamlit provides a minimal dashboard without adding frontend build tooling. It is intentionally API-driven and uses the same store/time filters as the backend endpoints.

## Environment Configuration

The project keeps local defaults but allows key paths and pipeline settings to be configured through environment variables. `.env.example` documents the supported settings.
