# API Reference

## GET /

Purpose: Confirm the API is running.

Parameters: None.

Request body: None.

Response body:

```json
{
  "message": "Store Intelligence API Running"
}
```

Error responses: None expected.

## GET /health

Purpose: Health check endpoint.

Parameters: None.

Request body: None.

Response body:

```json
{
  "status": "ok"
}
```

Error responses: None expected.

## GET /health/

Purpose: Health check endpoint.

Parameters: None.

Request body: None.

Response body:

```json
{
  "status": "ok"
}
```

Error responses: None expected.

## POST /events/ingest

Purpose: Ingest a batch of CCTV analytics events.

Parameters: None.

Request body: Array of event objects. Maximum batch size is 500.

```json
[
  {
    "event_id": "evt_001",
    "store_id": "STORE_001",
    "camera_id": "CAM_ENTRY_01",
    "visitor_id": "VIS_001",
    "event_type": "ENTRY",
    "timestamp": "2026-03-03T14:22:10Z",
    "zone_id": null,
    "dwell_ms": 0,
    "is_staff": false,
    "confidence": 0.95,
    "metadata": {
      "session_seq": 1,
      "track_id": 7,
      "frame": 42
    }
  }
]
```

Response body:

```json
{
  "success": true,
  "inserted_count": 1,
  "duplicate_count": 0,
  "failed_count": 0,
  "failed_events": []
}
```

Error responses:

```json
{
  "success": false,
  "message": "Maximum batch size exceeded"
}
```

```json
{
  "success": false,
  "message": "Internal server error"
}
```

## GET /stores/{store_id}/metrics

Purpose: Return business metrics for a store.

Path parameters:

- `store_id`: Store identifier.

Query parameters:

- `start_time`: Optional ISO datetime lower bound.
- `end_time`: Optional ISO datetime upper bound.

Request body: None.

Response body:

```json
{
  "store_id": "STORE_001",
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

Error responses:

```json
{
  "message": "No events found for store"
}
```

## POST /sessions/rebuild

Purpose: Reconstruct visitor sessions from historical events.

Parameters: None.

Request body:

```json
{
  "store_id": "STORE_001"
}
```

Response body:

```json
{
  "store_id": "STORE_001",
  "sessions_created": 125,
  "sessions_updated": 45
}
```

Error responses:

```json
{
  "success": false,
  "message": "Internal server error"
}
```

## GET /stores/{store_id}/funnel

Purpose: Return customer journey funnel analytics for a store.

Path parameters:

- `store_id`: Store identifier.

Query parameters:

- `start_time`: Optional ISO datetime lower bound.
- `end_time`: Optional ISO datetime upper bound.

Request body: None.

Response body:

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

Error responses:

```json
{
  "message": "No events found for store"
}
```

```json
{
  "success": false,
  "message": "Internal server error"
}
```

## GET /stores/{store_id}/anomalies

Purpose: Return operational anomalies for a store.

Path parameters:

- `store_id`: Store identifier.

Query parameters:

- `start_time`: Optional ISO datetime lower bound.
- `end_time`: Optional ISO datetime upper bound.

Request body: None.

Response body:

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

Error responses:

```json
{
  "message": "No events found for store"
}
```

```json
{
  "success": false,
  "message": "Internal server error"
}
```

## GET /stores/{store_id}/queue-status

Purpose: Return queue performance and congestion status.

Path parameters:

- `store_id`: Store identifier.

Query parameters:

- `start_time`: Optional ISO datetime lower bound.
- `end_time`: Optional ISO datetime upper bound.

Request body: None.

Response body:

```json
{
  "store_id": "STORE_001",
  "queue_joins": 120,
  "queue_abandons": 25,
  "abandonment_rate": 20.83,
  "status": "CONGESTED"
}
```

Error responses:

```json
{
  "message": "No events found for store"
}
```

```json
{
  "success": false,
  "message": "Internal server error"
}
```

## GET /stores/{store_id}/heatmap

Purpose: Return zone-level heatmap analytics for a store.

Path parameters:

- `store_id`: Store identifier.

Query parameters:

- `start_time`: Optional ISO datetime lower bound.
- `end_time`: Optional ISO datetime upper bound.

Request body: None.

Response body:

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

Error responses:

```json
{
  "message": "No events found for store"
}
```

```json
{
  "success": false,
  "message": "Internal server error"
}
```
