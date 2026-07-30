from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from os import getenv
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from qwen_classifier.model import ModelLoadError, TeamQwenClassifier


def _bool_env(name: str, default: bool = False) -> bool:
    value = getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


classifier = TeamQwenClassifier(
    Path(getenv("QWEN_MODEL_ROOT", "/model")),
    allow_remote_code=_bool_env("QWEN_ALLOW_REMOTE_CODE", False),
    device_map=getenv("QWEN_DEVICE_MAP", "auto"),
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_task: asyncio.Task[None] | None = None
    if _bool_env("QWEN_EAGER_LOAD", True):
        load_task = asyncio.create_task(asyncio.to_thread(classifier.load))
        app.state.model_load_task = load_task
    yield
    if load_task is not None and not load_task.done():
        load_task.cancel()


app = FastAPI(
    title="SafeMaint Team Qwen Accident Classifier",
    version="0.1.0",
    lifespan=lifespan,
)


class ClassificationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=2, max_length=5000)


class ClassificationResponse(BaseModel):
    label: str
    model: str
    adapter: str


def get_classifier() -> TeamQwenClassifier:
    return classifier


@app.get("/health/live")
def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", response_model=None)
def health_ready(
    model: TeamQwenClassifier = Depends(get_classifier),
) -> JSONResponse | dict[str, str]:
    if model.ready:
        return {"status": "ready", "model": model.model_id}
    detail = "model is loading" if not model.error else "model load failed"
    return JSONResponse(status_code=503, content={"status": detail})


@app.post("/v1/classify", response_model=ClassificationResponse)
def classify_accident(
    payload: ClassificationRequest,
    model: TeamQwenClassifier = Depends(get_classifier),
) -> ClassificationResponse:
    try:
        label = model.classify(payload.title, payload.text)
    except ModelLoadError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return ClassificationResponse(
        label=label,
        model=model.model_id,
        adapter=model.adapter_name,
    )
