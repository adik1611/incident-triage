"""Verifier/Router agent as its own service (independent failure domain, A2)."""
import time
from fastapi import FastAPI, Request, Response
from .verifier import verify_and_route
from . import metrics
from .config import settings

settings.SERVICE_NAME = "verifier"
app = FastAPI(title="Verifier/Router agent")

@app.post("/verify")
async def verify(payload: dict, request: Request):
    t0 = time.time()
    out = verify_and_route(payload)
    metrics.HTTP_SEC.labels("/verify").observe(time.time() - t0)
    return out

@app.get("/health")
def health(): return {"status": "ok"}

@app.get("/metrics")
def m():
    body, ct = metrics.render(); return Response(body, media_type=ct)
