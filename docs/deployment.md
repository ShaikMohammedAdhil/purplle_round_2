# Deployment

## Local Deployment

Create a virtual environment:

```bash
python -m venv .venv
```

Activate the environment.

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

macOS or Linux:

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the API:

```bash
uvicorn app.main:app --reload
```

Open the API docs:

```text
http://127.0.0.1:8000/docs
```

## Docker Deployment

Build and start the API service:

```bash
docker compose up --build
```

The compose file mounts the project directory into the container and runs Uvicorn with reload enabled for local development.

The Docker image includes a healthcheck that calls:

```text
http://127.0.0.1:8000/health
```

## Environment Configuration

Use `.env.example` as the reference for local overrides. The application works with defaults when no environment variables are set. For direct local runs, export the variables in your shell before starting Uvicorn, Streamlit, or the pipeline. Docker Compose passes `STORE_DB_PATH` to the API container with a local default and can read overrides from a `.env` file.

Supported API and dashboard settings:

- `STORE_DB_PATH`: SQLite database path. Defaults to `data/store.db`.
- `STORE_API_URL`: Dashboard API base URL. Defaults to `http://127.0.0.1:8000`.

Supported pipeline settings:

- `PIPELINE_API_BASE_URL`: API base URL for event upload.
- `PIPELINE_MODEL_PATH`: YOLO model path or model name.
- `PIPELINE_VIDEO_SOURCE`: Default input video path.
- `PIPELINE_OUTPUT_DIRECTORY`: Detection and event JSONL output directory.
- `PIPELINE_ZONES_PATH`: Zone configuration JSON path.
- `PIPELINE_STORE_ID` and `PIPELINE_CAMERA_ID`: Event identity fields.

## Production Considerations

For production deployment:

- Run Uvicorn behind a production process manager or ASGI server setup.
- Disable development reload.
- Use environment-based configuration for database paths and runtime settings.
- Use a managed relational database when write concurrency increases.
- Add authentication and authorization before exposing operational analytics externally.
- Configure structured log collection.
- Add request rate limits for ingestion endpoints.
- Add migrations before schema evolution.

## Logging

The API emits structured JSON logs for major request flows.

Logged fields include:

- `request_id`
- `endpoint`
- `store_id` where applicable
- `batch_size` for ingestion
- inserted, duplicate, and failed counts for ingestion
- `processing_time_ms`
- `status_code`
- `error_type`
- `error_message`

Production deployments should forward logs to centralized storage for search, alerting, and incident review.

## Database

The local database is SQLite at:

```text
data/store.db
```

The file is created automatically on startup by `init_db()`.

SQLite is appropriate for the take-home challenge and local development. For production scale, consider PostgreSQL with migrations, connection pooling, backups, and stricter transaction isolation.

## Scalability Notes

The current service uses efficient SQLAlchemy aggregate queries for analytics and avoids loading unnecessary rows for core metrics.

Potential scale improvements:

- Move from SQLite to PostgreSQL.
- Add database indexes for high-volume time-window queries.
- Store derived aggregates for frequent dashboard queries.
- Run session rebuild as an asynchronous background job.
- Split ingestion from analytics with a queue when event volume grows.
- Add observability around slow queries and endpoint latency.
