from typing import Protocol

from app.schemas.assessment import AssessmentRequest, EvidenceItem


class RetrievalService(Protocol):
    async def search(
        self, request: AssessmentRequest, limit: int = 5
    ) -> list[EvidenceItem]: ...


class NotConfiguredRetrievalService:
    """Safe placeholder until BM25, BGE-M3, and a reranker are connected."""

    async def search(
        self, request: AssessmentRequest, limit: int = 5
    ) -> list[EvidenceItem]:
        del request, limit
        return []
