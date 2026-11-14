"""Stub model provider for load tests (D6): fixed OpenAI-compatible response after a realistic delay."""
import asyncio, os
from fastapi import FastAPI
from .config import settings
app = FastAPI(title="LLM stub")
FAULT = {"down": False}

@app.post("/v1/chat/completions")
async def chat(body: dict):
    if FAULT["down"]: 
        from fastapi import HTTPException; raise HTTPException(503, "provider unavailable (injected)")
    await asyncio.sleep(settings.STUB_DELAY_MS / 1000)
    text = "The alert pattern is most consistent with the cause named in the cited guidance. Steps are proposals for the on-call engineer."
    return {"choices": [{"message": {"role": "assistant", "content": text}}], "usage": {"prompt_tokens": 420, "completion_tokens": 40}}

@app.post("/admin/fault")
def fault(body: dict): FAULT["down"] = bool(body.get("down")); return FAULT

@app.get("/health")
def health(): return {"status": "ok"}

from fastapi import Response
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
@app.get("/metrics")
def metrics_():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
