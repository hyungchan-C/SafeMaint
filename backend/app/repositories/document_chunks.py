from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, defer, selectinload

from app.db.models import DocumentChunk


class DocumentChunkRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_for_document_versions(
        self, document_version_ids: set[UUID]
    ) -> list[DocumentChunk]:
        if not document_version_ids:
            return []
        statement = (
            select(DocumentChunk)
            .where(DocumentChunk.document_version_id.in_(document_version_ids))
            .options(
                # embedding은 1024차원 벡터(청크당 십수 KB)라 매번 가져와 파싱하면
                # 느린데, 이 청크들은 PDF 페이지 카드용 텍스트 매칭에만 쓰이고
                # 벡터는 전혀 안 씀 — 실측상 이 한 줄로 청크 300여 개 조회가
                # 약 4배(400ms대 -> 100ms대) 빨라짐.
                defer(DocumentChunk.embedding),
                selectinload(DocumentChunk.document),
                selectinload(DocumentChunk.document_version),
            )
            .order_by(DocumentChunk.chunk_index)
        )
        return list(self.session.scalars(statement).all())
