"""
PDF 한 개를 받아서 전처리 -> DB 적재까지 수행.

임베딩은 여기서 만들지 않는다. 청크는 process_pdf()가 이미 채워주는
embedding_status="pending" 그대로 저장되고, 대기 중인 청크를 실제로 임베딩하는
건 backend/app/services/document_embeddings.py의 embed_pending_chunks()가
별도 배치로 처리한다 (적재와 임베딩을 분리하기로 팀원과 합의한 구조).

업로드 API(F-01)가 아직 없으므로 지금은 이 함수를 직접 호출하거나
run_ingest_*.py 스크립트로 실행한다. API가 생기면 그 라우트가 이 함수를
그대로 호출하면 된다.
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "preprocessing"))

from db_loader import load_document  # noqa: E402
from pdf_pipeline import process_pdf  # noqa: E402


def ingest_pdf(
    pdf_path: str,
    product_type: str,
    model_name: str,
    manufacturer: str = "오토닉스",
    exclude_sections: list[str] | None = None,
    chunk_size: int = 600,
    overlap: int = 100,
    source_type: str = "manual",
    document_type_code: str = "equipment_manual",
    access_level: str = "restricted",
) -> dict:
    """PDF -> 청크(embedding_status="pending") -> DB 적재. {"document_id", "chunk_count"}를 반환."""
    result = process_pdf(
        pdf_path,
        product_type=product_type,
        model_name=model_name,
        manufacturer=manufacturer,
        exclude_sections=exclude_sections,
        chunk_size=chunk_size,
        overlap=overlap,
        source_type=source_type,
        document_type_code=document_type_code,
        access_level=access_level,
    )
    document_id = load_document(result["document"], result["chunks"])
    return {"document_id": document_id, "chunk_count": len(result["chunks"])}
