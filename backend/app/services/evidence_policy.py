from __future__ import annotations


NO_EVIDENCE_WARNING = (
    "검색 범위에서 질문과 일치하는 검증 가능한 문서 근거를 찾지 못했습니다."
)

RAG_UNAVAILABLE_WARNING = (
    "문서 검색 서비스에 연결하지 못했습니다. "
    "서비스가 복구되기 전에는 구체적인 작업 절차를 안내할 수 없습니다."
)


def format_no_evidence_answer() -> str:
    """Return a topic-neutral response when approved evidence was not retrieved.

    Equipment, component, hazard, and work-procedure details must come from
    retrieved documents rather than source-code branches.
    """

    return (
        "질문과 일치하는 검증 가능한 문서 근거를 찾지 못했습니다.\n\n"
        "확인에 필요한 자료:\n"
        "- 설비 제조사와 정확한 모델·부품번호\n"
        "- 승인된 제조사 매뉴얼 또는 사업장 작업표준\n"
        "- 수행하려는 작업 범위와 차단해야 할 에너지원\n\n"
        "관련 문서를 등록하거나 검색 범위를 확인한 뒤 다시 질문해 주세요. "
        "근거가 확인되기 전에는 구체적인 작업 절차, 설정값 또는 작업 승인 여부를 안내할 수 없습니다."
    )
