# Testing

## Overview

The test suite uses Pytest and FastAPI `TestClient`.

Tests create isolated in-memory SQLite databases and override `get_db()` so test runs do not mutate `data/store.db`.

Run all tests:

```bash
pytest
```

Run a specific test file:

```bash
pytest tests/test_ingest.py
pytest tests/test_metrics.py
pytest tests/test_funnel.py
pytest tests/test_anomalies.py
pytest tests/test_heatmap.py
pytest tests/test_tracker.py
pytest tests/test_event_generation.py
pytest tests/test_hardening_integration.py
```

## Test Files

### tests/test_ingest.py

Validates Phase 3 ingestion behavior:

- Valid event insertion
- Duplicate event handling
- Invalid confidence validation
- Batch size limit
- Partial success behavior
- Missing required field handling
- Invalid event type handling

### tests/test_metrics.py

Validates Phase 4 metrics behavior:

- Successful metrics response
- 404 when no events exist
- Conversion rate calculation
- Abandonment rate calculation
- Date filtering
- Peak hour calculation
- Average dwell calculation

### tests/test_funnel.py

Validates Phase 5 session and funnel behavior:

- Session creation from `ENTRY`
- Session closure from `EXIT`
- Dwell accumulation
- Conversion tracking
- Re-entry handling
- Session rebuild endpoint
- Funnel endpoint success
- Funnel 404 response
- Engagement rate calculation
- Conversion rate calculation
- Rebuild failure 500 response
- Funnel service failure 500 response
- Rollback verification on rebuild failure

### tests/test_anomalies.py

Validates Phase 6 anomaly and queue behavior:

- Long dwell anomaly
- Queue congestion anomaly
- Traffic spike anomaly
- Low conversion anomaly
- Empty store anomaly
- Queue status `NORMAL`
- Queue status `BUSY`
- Queue status `CONGESTED`
- 404 handling
- 500 handling

### tests/test_heatmap.py

Validates Phase 11 heatmap behavior:

- Normal heatmap response
- Multiple-zone heat score calculation
- Empty store 404 response
- Date filtering
- Invalid store handling
- Unexpected service failure handling

### tests/test_tracker.py

Validates Phase 9 tracking behavior:

- Track creation
- Stable track IDs across frames
- New track creation for distant detections
- Frame-grouped tracking output

### tests/test_event_generation.py

Validates Phase 9 business event generation:

- Visitor ID creation
- Zone entry and exit event generation
- Zone dwell event generation
- JSONL output creation
- Re-entry detection
- Billing queue join and abandon events
- Successful billing exit without false abandonment

### tests/test_hardening_integration.py

Validates the pre-Phase-12 hardening path:

- Pipeline-generated event metadata is accepted by ingestion
- Mocked video detection flows through tracking, event generation, upload, and analytics
- Generated pipeline events persist to the database
- Session rebuild works after ingestion
- Metrics analytics can query the ingested data
- Required endpoint smoke paths return successful responses

## Coverage Summary

The test suite covers:

- Main happy paths for all implemented APIs
- Input validation for event ingestion
- Idempotency and duplicate handling
- Time-window filtering
- Business metric calculations
- Session state transitions
- Funnel and conversion calculations
- Operational anomaly detection
- Queue status classification
- Zone heatmap analytics
- Detection-to-track identity assignment
- Zone analytics and event emission
- Pipeline JSONL output generation
- Safe error response behavior

## Final Project Review

Backend application and test files were reviewed for:

- Incomplete implementation markers
- Placeholder code
- Pseudocode
- Dead obvious stubs
- Major duplicate services
- Major duplicate schemas

Findings:

- The implemented backend API files and tests are clean for submission.
- The pipeline now includes detection, tracking, zone analytics, visitor ID assignment, and JSONL event emission coverage.
- Response schemas are currently colocated with their owning service modules in several phases. This is consistent within the project, though a larger production codebase might centralize response schemas.

## Recommended Validation Before Submission

Run:

```bash
python -m py_compile app/*.py tests/*.py
pytest
```

Then start the API:

```bash
uvicorn app.main:app --reload
```

Confirm Swagger loads:

```text
http://127.0.0.1:8000/docs
```
