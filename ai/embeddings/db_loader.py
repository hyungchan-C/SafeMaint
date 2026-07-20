"""
process_pdf() + embed_texts() 결과를 Postgres(documents/document_chunks)에 적재.

스키마 중복 정의를 피하기 위해 backend의 SQLAlchemy 모델(app.db.models.document)과
세션(app.db.session)을 그대로 재사용합니다. docs/database.md에 이미 정리된
"PYTHONPATH=backend로 app.* 임포트" 관례를 그대로 따릅니다.
"""

import sys
from pathlib import Path
from typing import Any

_BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.append(str(_BACKEND_DIR))

from sqlalchemy import delete, select  # noqa: E402

from app.db.models.document import Document, DocumentChunk  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402


def load_document(document: dict[str, Any], chunks: list[dict[str, Any]]) -> str:
    """
    document/chunks 레코드를 DB에 적재. 같은 external_id 문서가 이미 있으면
    문서 필드를 갱신하고 청크는 통째로 교체한다(재업로드·재처리 시 멱등성 확보).
    반환값은 적재된 Document의 UUID 문자열.
    """
    session = SessionLocal()
    try:
        existing = session.scalar(
            select(Document).where(Document.external_id == document["external_id"])
        )

        if existing is not None:
            # 재처리 시 (document_id, chunk_index) unique 제약과 겹치지 않도록
            # 새 청크를 만들기 전에 기존 청크를 먼저 지우고 flush한다.
            session.execute(
                delete(DocumentChunk).where(DocumentChunk.document_id == existing.id)
            )
            session.flush()

        chunk_rows = [
            DocumentChunk(
                chunk_index=chunk["chunk_index"],
                page_number=chunk.get("page_number"),
                page_start=chunk.get("page_start"),
                page_end=chunk.get("page_end"),
                section_path=chunk["section_path"],
                content=chunk["content"],
                content_hash=chunk["content_hash"],
                metadata_json=chunk["metadata"],
                embedding=chunk.get("embedding"),
                embedding_model=chunk.get("embedding_model"),
                embedding_dimension=chunk.get("embedding_dimension"),
                embedding_status=chunk.get("embedding_status", "pending"),
            )
            for chunk in chunks
        ]

        if existing is None:
            row = Document(
                external_id=document["external_id"],
                title=document["title"],
                source_type=document["source_type"],
                document_type_code=document["document_type_code"],
                publisher=document.get("publisher"),
                source_url=document.get("source_url"),
                revision=document.get("revision"),
                published_at=document.get("published_at"),
                access_level=document["access_level"],
                file_sha256=document.get("file_sha256"),
                metadata_json=document["metadata"],
                chunks=chunk_rows,
            )
            session.add(row)
        else:
            row = existing
            row.title = document["title"]
            row.source_type = document["source_type"]
            row.document_type_code = document["document_type_code"]
            row.publisher = document.get("publisher")
            row.source_url = document.get("source_url")
            row.revision = document.get("revision")
            row.published_at = document.get("published_at")
            row.access_level = document["access_level"]
            row.file_sha256 = document.get("file_sha256")
            row.metadata_json = document["metadata"]
            row.chunks = chunk_rows

        session.commit()
        session.refresh(row)
        return str(row.id)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
