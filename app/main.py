from fastapi import FastAPI

from app import anomalies, funnel, health, heatmap, ingestion, metrics
from app.db import init_db

app = FastAPI(
    title="Store Intelligence API",
    description="Retail Store Analytics Platform",
    version="1.0.0",
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()

app.include_router(health.router)
app.include_router(ingestion.router)
app.include_router(metrics.router)
app.include_router(funnel.router)
app.include_router(anomalies.router)
app.include_router(heatmap.router)


@app.get("/")
def read_root() -> dict[str, str]:
    return {"message": "Store Intelligence API Running"}
