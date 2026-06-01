import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd
import streamlit as st


DEFAULT_API_URL = os.getenv("STORE_API_URL", "http://127.0.0.1:8000")


def fetch_json(
    *,
    api_url: str,
    path: str,
    params: dict[str, str] | None = None,
) -> dict[str, Any] | list[dict[str, Any]] | None:
    """Fetch JSON from the Store Intelligence API."""
    query = f"?{urlencode(params)}" if params else ""
    url = f"{api_url.rstrip('/')}{path}{query}"
    try:
        with urlopen(url, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 404:
            return None
        st.warning(f"{path} returned HTTP {exc.code}")
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        st.warning(f"Unable to load {path}: {exc}")
    return None


def query_params(start_time: str, end_time: str) -> dict[str, str]:
    """Build optional time-window query parameters."""
    params: dict[str, str] = {}
    if start_time.strip():
        params["start_time"] = start_time.strip()
    if end_time.strip():
        params["end_time"] = end_time.strip()
    return params


st.title("Store Intelligence Dashboard")

with st.sidebar:
    api_url = st.text_input("API URL", DEFAULT_API_URL)
    store_id = st.text_input("Store ID", "STORE_001")
    start_time = st.text_input("Start time")
    end_time = st.text_input("End time")
    refresh = st.button("Refresh")

params = query_params(start_time, end_time)

metrics = fetch_json(
    api_url=api_url,
    path=f"/stores/{store_id}/metrics",
    params=params,
)
funnel = fetch_json(
    api_url=api_url,
    path=f"/stores/{store_id}/funnel",
    params=params,
)
queue_status = fetch_json(
    api_url=api_url,
    path=f"/stores/{store_id}/queue-status",
    params=params,
)
anomalies = fetch_json(
    api_url=api_url,
    path=f"/stores/{store_id}/anomalies",
    params=params,
)
heatmap = fetch_json(
    api_url=api_url,
    path=f"/stores/{store_id}/heatmap",
    params=params,
)

st.header("Metrics")
if isinstance(metrics, dict):
    col1, col2, col3 = st.columns(3)
    col1.metric("Visitors", metrics.get("unique_visitors", 0))
    col2.metric("Entries", metrics.get("entries", 0))
    col3.metric("Conversion Rate", f"{metrics.get('conversion_rate', 0)}%")
else:
    st.info("No metrics available.")

st.header("Funnel")
if isinstance(funnel, dict):
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Entered", funnel.get("entered", 0))
    col2.metric("Engaged", funnel.get("engaged", 0))
    col3.metric("Queued", funnel.get("queue_visitors", 0))
    col4.metric("Converted", funnel.get("converted", 0))
else:
    st.info("No funnel data available.")

st.header("Queue Status")
if isinstance(queue_status, dict):
    st.metric(
        "Status",
        queue_status.get("status", "UNKNOWN"),
        f"{queue_status.get('abandonment_rate', 0)}% abandonment",
    )
else:
    st.info("No queue status available.")

st.header("Anomalies")
if isinstance(anomalies, dict) and anomalies.get("anomalies"):
    st.dataframe(pd.DataFrame(anomalies["anomalies"]), use_container_width=True)
else:
    st.info("No anomalies detected.")

st.header("Heatmap")
if isinstance(heatmap, list) and heatmap:
    heatmap_df = pd.DataFrame(heatmap)
    st.dataframe(heatmap_df, use_container_width=True)
    st.bar_chart(heatmap_df.set_index("zone_id")["heat_score"])
else:
    st.info("No heatmap data available.")

if refresh:
    st.rerun()
