from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import FastAPI, HTTPException, status

from rag_service.config import settings
from rag_service.retrieval import PgvectorRetriever
from rag_service.schemas import ChatResponse, ChatSource, InternalChatRequest
from evidence_policy import NO_EVIDENCE_WARNING, format_no_evidence_answer


app = FastAPI(
    title="SafeMaint Hybrid Retrieval Service",
    version="0.2.0",
)
retriever = PgvectorRetriever(settings)

def _grounded_excerpt_answer(sources: list[ChatSource]) -> str:
    lines = ["선택한 매뉴얼에서 질문과 관련된 다음 근거를 찾았습니다."]
    for index, source in enumerate(sources[:3], start=1):
        location_parts = []
        if source.document_version is not None:
            location_parts.append(f"버전 {source.document_version}")
        if source.page_start is not None:
            page_label = f"{source.page_start}쪽"
            if source.page_end and source.page_end != source.page_start:
                page_label = f"{source.page_start}-{source.page_end}쪽"
            location_parts.append(page_label)
        location = f" ({', '.join(location_parts)})" if location_parts else ""
        excerpt = " ".join(source.excerpt.split())
        lines.append(f"{index}. {source.title}{location}\n{excerpt}")
    lines.append(
        "정확한 작업 절차와 설정값은 위 인용 내용 및 해당 페이지 원문을 확인하고, "
        "현장 안전관리자의 최종 확인 후 적용하세요."
    )
    return "\n\n".join(lines)


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

    warning = None
    if not sources:
        warning = NO_EVIDENCE_WARNING
    if _unresolved_legacy_manuals(payload):
        manual_warning = (
            "기존 파일명 방식의 매뉴얼 선택값은 검색 범위로 사용하지 않았습니다. "
            "문서 UUID 또는 문서 버전 UUID를 사용해 주세요."
        )
        warning = f"{warning} {manual_warning}" if warning else manual_warning

    return ChatResponse(
        answer=(
            _grounded_excerpt_answer(sources)
            if sources
            else format_no_evidence_answer()
        ),
        sources=sources,
        retrieval_mode="hybrid",
        warning=warning,
    )
