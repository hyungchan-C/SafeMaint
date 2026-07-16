from __future__ import annotations

import asyncio

from fastapi import FastAPI, HTTPException, status

from rag_service.config import settings
from rag_service.retrieval import PgvectorRetriever
from rag_service.schemas import ChatRequest, ChatResponse
from safety_guidance import format_safety_answer


app = FastAPI(
    title="SafeMaint BGE-M3 Retrieval Service",
    version="0.1.0",
)
retriever = PgvectorRetriever(settings)


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
            detail="RAG 데이터베이스에 연결할 수 없습니다.",
        ) from exc
    if count == 0:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="검색 가능한 임베딩 청크가 없습니다.",
        )
    return {"status": "ready", "ready_chunks": count, "model": settings.model_name}


@app.post("/v1/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest) -> ChatResponse:
    try:
        sources = await asyncio.to_thread(retriever.search, payload)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="BGE-M3 임베딩 또는 pgvector 검색에 실패했습니다.",
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
        warning = (
            "현재 유사도 기준을 충족한 사고사례가 없어 공통 안전수칙만 표시했습니다. "
            "제조사 매뉴얼과 현장 조건을 별도로 확인하세요."
        )
    if payload.context.registered_manuals:
        manual_warning = (
            "화면에서 선택한 매뉴얼은 아직 파일 업로드·전처리되지 않아 이번 검색 근거에 포함되지 않았습니다."
        )
        warning = f"{warning} {manual_warning}" if warning else manual_warning

    return ChatResponse(
        answer=format_safety_answer(
            context_text,
            source_titles=[source.title for source in sources],
        ),
        sources=sources,
        warning=warning,
    )
