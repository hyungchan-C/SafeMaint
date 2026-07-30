from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from qwen_service.config import settings
from qwen_service.model import QwenAnswerGenerationError, QwenEngine
from qwen_service.schemas import (
    AnswerRequest,
    AnswerResponse,
    ClassifyRequest,
    ClassifyResponse,
    DocumentProfileRequest,
    DocumentProfileResponse,
    IntentClassifyResponse,
)


app = FastAPI(title="SafeMaint Qwen Service", version="0.1.0")
security = HTTPBearer(auto_error=False)
engine = QwenEngine(settings)
logger = logging.getLogger(__name__)


def require_api_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> None:
    if not settings.api_key:
        return
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Qwen API key is required.",
        )
    if credentials.credentials != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid Qwen API key.",
        )


@app.get("/health/live")
def live() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "model": settings.base_model,
        "adapter_configured": bool(settings.lora_adapter),
    }


@app.post("/v1/classify", response_model=ClassifyResponse)
async def classify(
    payload: ClassifyRequest,
    _: None = Depends(require_api_key),
) -> ClassifyResponse:
    return await engine.classify(payload)


@app.post("/v1/intent", response_model=IntentClassifyResponse)
async def intent(
    payload: ClassifyRequest,
    _: None = Depends(require_api_key),
) -> IntentClassifyResponse:
    return await engine.intent(payload)


@app.post("/v1/answer", response_model=AnswerResponse)
async def answer(
    payload: AnswerRequest,
    _: None = Depends(require_api_key),
) -> AnswerResponse:
    try:
        return await engine.answer(payload)
    except QwenAnswerGenerationError as exc:
        logger.warning("Qwen answer validation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Qwen did not produce a valid compact answer.",
        ) from exc
    except Exception:
        logger.exception("Qwen answer generation failed.")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Qwen answer generation failed.",
        )


@app.post("/v1/document-profile", response_model=DocumentProfileResponse)
async def document_profile(
    payload: DocumentProfileRequest,
    _: None = Depends(require_api_key),
) -> DocumentProfileResponse:
    try:
        return await engine.document_profile(payload)
    except Exception:
        logger.exception("Qwen document profile extraction failed.")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Qwen document profile extraction failed.",
        )
