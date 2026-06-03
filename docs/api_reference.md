# API Reference

Base URL for local development:

```text
http://127.0.0.1:8000
```

Datetime query parameters use ISO 8601 strings, for example `2026-03-01T00:00:00Z`.

## GET /

Confirms the API process is running.

Example:

```bash
curl "http://127.0.0.1:8000/"
```

Response:

```json
{
  "message": "Store Intelligence API Running"
}
```

## GET /health

Health check endpoint. `/health/` is also supported.

Example:

```bash
curl "http://127.0.0.1:8000/health"
```

Response:

```json
{
  "status": "ok"
}
```

## POST /events/ingest

Ingests a batch of CCTV analytics events. The request body must be a JSON array with at most 500 events.

Example:

```bash
curl -X POST "http://127.0.0.1:8000/events/ingest" \
  -H "Content-Type: application/json" \
  -d '[
    {
      "event_id": "evt_001",
      "store_id": "STORE_001",
      "camera_id": "CAM_1",
      "visitor_id": "VISITOR_000001",
      "event_type": "ENTRY",
      "timestamp": "2026-03-03T14:22:10Z",
      "zone_id": "ENTRY_ZONE",
      "dwell_ms": 0,
      "is_staff": false,
      "confidence": 0.95,
      "metadata": {
        "track_id": 7,
        "frame": 42,
        "session_seq": 1
      }
    }
  ]'
```

Success response:

```json
{
  "success": true,
  "inserted_count": 1,
  "duplicate_count": 0,
  "failed_count": 0,
  "failed_events": []
}
```

Partial-success response:

```json
{
  "success": true,
  "inserted_count": 1,
  "duplicate_count": 0,
  "failed_count": 1,
  "failed_events": [
    {
      "event_id": "evt_bad",
      "reason": "invalid confidence"
    }
  ]
}
```

Request-level error examples:

```json
{
  "success": false,
  "message": "Request body must be a list"
}
```

```json
{
  "success": false,
  "message": "Maximum batch size exceeded"
}
```

Supported event types:

- `ENTRY`
- `EXIT`
- `ZONE_ENTER`
- `ZONE_EXIT`
- `ZONE_DWELL`
- `BILLING_QUEUE_JOIN`
- `BILLING_QUEUE_ABANDON`
- `REENTRY`

## GET /stores/{store_id}/metrics

Returns aggregate store metrics from persisted events. Metrics are store-level and include events across all cameras for the selected store.

Query parameters:

- `start_time`: optional ISO datetime lower bound.
- `end_time`: optional ISO datetime upper bound.

Example:

```bash
curl "http://127.0.0.1:8000/stores/STORE_001/metrics?start_time=2026-03-01T00:00:00Z&end_time=2026-03-31T23:59:59Z"
```

Response:

```json
{
  "store_id": "STORE_001",
  "camera_count": 3,
  "unique_visitors": 120,
  "entries": 140,
  "exits": 132,
  "staff_entries": 8,
  "average_dwell_time_seconds": 245.5,
  "peak_hour": "15:00",
  "queue_joins": 30,
  "queue_abandons": 5,
  "abandonment_rate": 16.67,
  "conversion_rate": 42.5
}
```

No events response:

```json
{
  "message": "No events found for store"
}
```

## POST /sessions/rebuild

Reconstructs visitor sessions for a store by replaying historical events. Run this after ingesting events when funnel/session analytics need to reflect the latest event history.

Example:

```bash
curl -X POST "http://127.0.0.1:8000/sessions/rebuild" \
  -H "Content-Type: application/json" \
  -d '{"store_id": "STORE_001"}'
```

Response:

```json
{
  "store_id": "STORE_001",
  "sessions_created": 125,
  "sessions_updated": 45
}
```

Unexpected error response:

```json
{
  "success": false,
  "message": "Internal server error"
}
```

## GET /stores/{store_id}/funnel

Returns customer journey funnel metrics.

Query parameters:

- `start_time`: optional ISO datetime lower bound.
- `end_time`: optional ISO datetime upper bound.

Example:

```bash
curl "http://127.0.0.1:8000/stores/STORE_001/funnel"
```

Response:

```json
{
  "store_id": "STORE_001",
  "entered": 500,
  "engaged": 320,
  "queue_visitors": 120,
  "converted": 90,
  "engagement_rate": 64.0,
  "queue_rate": 24.0,
  "conversion_rate": 18.0
}
```

## GET /stores/{store_id}/anomalies

Returns detected operational anomalies.

Query parameters:

- `start_time`: optional ISO datetime lower bound.
- `end_time`: optional ISO datetime upper bound.

Example:

```bash
curl "http://127.0.0.1:8000/stores/STORE_001/anomalies"
```

Response:

```json
{
  "store_id": "STORE_001",
  "anomaly_count": 1,
  "anomalies": [
    {
      "type": "QUEUE_CONGESTION",
      "severity": "HIGH",
      "message": "Queue abandonment exceeds threshold",
      "value": 28.4
    }
  ]
}
```

Implemented anomaly types:

- `LONG_DWELL_TIME`
- `QUEUE_CONGESTION`
- `TRAFFIC_SPIKE`
- `LOW_CONVERSION`
- `EMPTY_STORE`

## GET /stores/{store_id}/queue-status

Returns queue performance and congestion status.

Query parameters:

- `start_time`: optional ISO datetime lower bound.
- `end_time`: optional ISO datetime upper bound.

Example:

```bash
curl "http://127.0.0.1:8000/stores/STORE_001/queue-status"
```

Response:

```json
{
  "store_id": "STORE_001",
  "queue_joins": 120,
  "queue_abandons": 25,
  "abandonment_rate": 20.83,
  "status": "CONGESTED"
}
```

Queue statuses:

- `NORMAL`: abandonment rate is less than 10%.
- `BUSY`: abandonment rate is at least 10% and less than 20%.
- `CONGESTED`: abandonment rate is at least 20%.

## GET /stores/{store_id}/heatmap

Returns zone-level heatmap analytics.

Query parameters:

- `start_time`: optional ISO datetime lower bound.
- `end_time`: optional ISO datetime upper bound.

Example:

```bash
curl "http://127.0.0.1:8000/stores/STORE_001/heatmap"
```

Response:

```json
[
  {
    "zone_id": "MAKEUP_ZONE",
    "visit_count": 120,
    "unique_visitors": 90,
    "avg_dwell_ms": 45000.0,
    "heat_score": 87
  }
]
```

`heat_score` is normalized to the `0` to `100` range across returned zones.
