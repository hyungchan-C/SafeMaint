from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import FastAPI, HTTPException, status

from rag_service.config import settings
from rag_service.retrieval import PgvectorRetriever
from rag_service.schemas import ChatResponse, InternalChatRequest
from safety_guidance import format_safety_answer


app = FastAPI(
    title="SafeMaint Hybrid Retrieval Service",
    version="0.2.0",
)
retriever = PgvectorRetriever(settings)

NO_EVIDENCE_ANSWER = (
    "질문과 일치하는 검증 가능한 문서 근거를 찾지 못했습니다. "
    "설비명·부품명·모델명과 작업 내용을 더 구체적으로 입력하거나 승인된 매뉴얼을 선택해 주세요. "
    "근거를 확인하기 전에는 작업 승인 여부를 판단하지 마세요."
)


@app.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
def ready() -> dict[str, object]:
    try:
        count = retriever.ready_chunk_count()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RAG database connection failed.",
        ) from exc
    if count == 0:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No searchable public chunks are ready.",
        )
    return {"status": "ready", "ready_chunks": count, "model": settings.model_name}


def _unresolved_legacy_manuals(payload: InternalChatRequest) -> list[str]:
    unresolved: list[str] = []
    for value in payload.context.registered_manuals:
        try:
            UUID(value)
        except (TypeError, ValueError):
            unresolved.append(value)
    return unresolved


@app.post("/v1/chat", response_model=ChatResponse)
async def chat(payload: InternalChatRequest) -> ChatResponse:
    try:
        sources = await asyncio.to_thread(retriever.search, payload)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Hybrid document retrieval failed.",
        ) from exc

    context_text = " ".join(
        value
        for value in (
            payload.context.equipment_name,
            payload.context.component_name,
            payload.context.task_type,
            payload.question,
        )
        if value
    )
    warning = None
    if not sources:
        warning = "검색 범위에서 질문 주제와 일치하는 근거를 찾지 못했습니다."
    if _unresolved_legacy_manuals(payload):
        manual_warning = (
            "기존 파일명 방식의 매뉴얼 선택값은 검색 범위로 사용하지 않았습니다. "
            "문서 UUID 또는 문서 버전 UUID를 사용해 주세요."
        )
        warning = f"{warning} {manual_warning}" if warning else manual_warning

    return ChatResponse(
        answer=(
            format_safety_answer(
                context_text,
                source_titles=[source.title for source in sources],
            )
            if sources
            else NO_EVIDENCE_ANSWER
        ),
        sources=sources,
        retrieval_mode="hybrid",
        warning=warning,
    )
