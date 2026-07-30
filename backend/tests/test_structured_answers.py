import json

import pytest
from pydantic import ValidationError

from app.schemas.chat import (
    ChatChecklistItem,
    ChatSource,
    ComponentAnswerDetails,
    DocumentAnswerDetails,
    EvidenceBackedItem,
    EvidenceConflict,
    MaintenanceAnswerDetails,
    MaintenanceHazard,
    MaintenanceSummary,
)
from app.services.structured_answers import (
    _card_meaning_pages,
    _component_usage_phrase,
    _clean_source_excerpt,
    _document_entity_phrase,
    _manual_step_phrase,
    _pre_check_phrase,
    _polite_document_sentence,
    _question_subject_phrase,
    enriched_structured_answer,
    finalize_document_answer,
    finalize_maintenance_answer,
    repair_extracted_quantity_order,
    source_based_checklist_items,
    source_based_fallback,
    validated_checklist_items,
    validated_structured_answer,
    RATING_PERFORMANCE_SECTION_SIGNALS,
)


def test_displaced_duration_and_repetition_tokens_are_repaired() -> None:
    malformed = (
        "정상 운전을 하기 전에 판넬의 스위치를 정회전과 역회전 "
        "초 이상 을 회 이상 (5~6 ) 3 반복하여 이물질이 끼어있는지 확인한다."
    )

    repaired = repair_extracted_quantity_order(malformed)

    assert "정회전과 역회전(5~6초 이상)을 3회 이상 반복" in repaired
    assert "초 이상 을 회 이상" not in repaired


def test_inspection_scope_exemption_is_not_used_as_pre_work_check() -> None:
    scope_text = (
        "컨베이어 안전검사 적용범위. 다만 다음 각 목의 어느 하나에 해당하는 "
        "것 또는 구간은 제외한다. 점검문을 열면 컨베이어 시스템이 정지하는 경우 "
        "점검문을 열어도 내부에 철망 감응형 방호장치 등이 설치되어 있는 경우, "
        "자 산업용 로봇 셀 내에 설치된 것으로 사람의 접근이 불가능한 구간."
    )

    assert _pre_check_phrase(scope_text) == ""


@pytest.mark.parametrize(
    "scope_fragment",
    [
        "점검문을 열면 컨베이어 시스템이 정지하는 경우",
        "점검문을 열어도 내부에 철망 감응형 방호장치 등이 설치되어 있는 경우, 자 산업",
    ],
)
def test_truncated_inspection_scope_fragment_is_not_a_pre_work_check(
    scope_fragment: str,
) -> None:
    assert _pre_check_phrase(scope_fragment) == ""


@pytest.mark.parametrize(
    ("raw_text", "expected"),
    [
        (
            "청소 및 점검 수리 등 작업을 완료한 해당 작업자가 태그를 제거하고 전원",
            "작업 완료 후 담당 작업자만 잠금·표지를 제거하는지 확인합니다.",
        ),
        (
            "시건한 스위치에는 청소 또는 점검 수리 작업 중 조작금지 태그를 (Switch) '",
            "전원 차단 스위치에 조작금지 표지가 부착되어 있는지 확인합니다.",
        ),
    ],
)
def test_loto_table_fragments_become_complete_pre_work_checks(
    raw_text: str,
    expected: str,
) -> None:
    assert _pre_check_phrase(raw_text) == expected


def test_truncated_power_switch_state_is_completed_for_each_section() -> None:
    raw_text = "청소 및 점검 수리 등 작업 시 반드시 전원차단 스위치를 상태로"

    assert _pre_check_phrase(raw_text) == (
        "청소·점검·수리 전 전원 차단 스위치가 차단 위치인지 확인합니다."
    )
    assert _manual_step_phrase(raw_text) == (
        "청소·점검·수리 전 전원 차단 스위치를 차단 위치로 전환합니다."
    )


def test_finalizer_normalizes_qwen_truncated_manual_step() -> None:
    raw_text = "청소 및 점검 수리 등 작업 시 반드시 전원차단 스위치를 상태로"
    answer = MaintenanceAnswerDetails(
        summary=MaintenanceSummary(
            status="안전관리자 확인 필요",
            risk_level="판단 불가",
            risk_basis=[],
            core_warning="작업 전 확인 필요",
        ),
        manual_steps=[
            EvidenceBackedItem(
                content=raw_text,
                evidence_chunk_ids=["manual-1"],
            )
        ],
        evidence_chunk_ids=["manual-1"],
    )

    finalized = finalize_maintenance_answer(
        answer,
        sources=[],
        question="컨베이어 벨트를 교체해야 해.",
    )

    assert isinstance(finalized, MaintenanceAnswerDetails)
    assert [item.content for item in finalized.manual_steps] == [
        "청소·점검·수리 전 전원 차단 스위치를 차단 위치로 전환합니다."
    ]


def test_enrichment_keeps_incident_references_missing_from_qwen_answer() -> None:
    qwen = MaintenanceAnswerDetails(
        summary=MaintenanceSummary(
            status="안전관리자 확인 필요",
            risk_level="보통",
            risk_basis=[],
            core_warning="작업 전 확인 필요",
        ),
        related_regulations_and_incidents=[
            EvidenceBackedItem(
                content="컨베이어 안전 가이드",
                evidence_chunk_ids=["guide-1"],
            )
        ],
        evidence_chunk_ids=["guide-1"],
    )
    fallback = MaintenanceAnswerDetails(
        summary=MaintenanceSummary(
            status="안전관리자 확인 필요",
            risk_level="보통",
            risk_basis=[],
            core_warning="작업 전 확인 필요",
        ),
        related_regulations_and_incidents=[
            EvidenceBackedItem(
                content="컨베이어 협착 사고사례",
                evidence_chunk_ids=["incident-1"],
            )
        ],
        evidence_chunk_ids=["incident-1"],
    )

    enriched = enriched_structured_answer(
        qwen,
        fallback,
        expected_type="maintenance_guide",
    )

    assert isinstance(enriched, MaintenanceAnswerDetails)
    assert [
        item.evidence_chunk_ids
        for item in enriched.related_regulations_and_incidents
    ] == [["guide-1"], ["incident-1"]]
    assert enriched.evidence_chunk_ids == ["guide-1", "incident-1"]


def test_enrichment_keeps_manual_steps_missing_from_short_qwen_answer() -> None:
    qwen = MaintenanceAnswerDetails(
        summary=MaintenanceSummary(
            status="안전관리자 확인 필요",
            risk_level="판단 불가",
            risk_basis=[],
            core_warning="작업 전 확인 필요",
        ),
        manual_steps=[
            EvidenceBackedItem(
                content="비상정지장치를 정기 점검합니다.",
                evidence_chunk_ids=["manual-1"],
            )
        ],
        evidence_chunk_ids=["manual-1"],
    )
    fallback = MaintenanceAnswerDetails(
        summary=qwen.summary,
        manual_steps=[
            EvidenceBackedItem(
                content="청소 및 점검 수리 등 작업 시 반드시 전원차단 스위치를 상태로",
                evidence_chunk_ids=["manual-1"],
            ),
            EvidenceBackedItem(
                content="정상 운전 전에 정회전과 역회전을 반복하여 이물질 여부를 확인합니다.",
                evidence_chunk_ids=["manual-2"],
            ),
        ],
        evidence_chunk_ids=["manual-1", "manual-2"],
    )

    enriched = enriched_structured_answer(
        qwen,
        fallback,
        expected_type="maintenance_guide",
    )
    finalized = finalize_maintenance_answer(
        enriched,
        sources=[],
        question="컨베이어 벨트를 교체해야 해.",
    )

    assert isinstance(finalized, MaintenanceAnswerDetails)
    assert [item.content for item in finalized.manual_steps] == [
        "비상정지장치를 정기 점검합니다.",
        "청소·점검·수리 전 전원 차단 스위치를 차단 위치로 전환합니다.",
        "정상 운전 전에 정회전과 역회전을 반복하여 이물질 여부를 확인합니다.",
    ]


def test_card_meaning_pages_finds_the_rating_performance_section() -> None:
    # "작업 안내" 탭의 이 카드는 어떤 문장이 만들어졌는지와 무관하게, 카드의 고정된
    # 의미(정격/성능 신호어)로 문서 전체에서 찾는다.
    unrelated_page = ChatSource(
        document_id="doc-x",
        chunk_id="unrelated-1",
        title="설비 매뉴얼",
        source_type="equipment_manual",
        section="1. 개요",
        excerpt="본 설비의 개요를 설명합니다.",
        similarity=0.5,
    )
    rating_page = ChatSource(
        document_id="doc-x",
        chunk_id="rating-1",
        title="설비 매뉴얼",
        source_type="equipment_manual",
        section="2. 정격 및 성능",
        excerpt="응답시간, 소비전류, 중량 등 정격값을 명시합니다.",
        similarity=0.5,
    )

    pages = _card_meaning_pages([unrelated_page, rating_page], RATING_PERFORMANCE_SECTION_SIGNALS)

    assert pages == ["rating-1"]


def test_card_meaning_pages_excludes_precaution_sections_for_rating_performance() -> None:
    precaution_page = ChatSource(
        document_id="doc-x",
        chunk_id="precaution-1",
        title="설비 매뉴얼",
        source_type="equipment_manual",
        section="안전을 위한 주의사항",
        # 신호어(정격/성능)가 들어있어도 섹션 자체가 주의사항이면 제외되어야 함.
        excerpt="정격 성능을 초과하여 사용하지 마십시오.",
        similarity=0.5,
    )
    rating_page = ChatSource(
        document_id="doc-x",
        chunk_id="rating-1",
        title="설비 매뉴얼",
        source_type="equipment_manual",
        section="2. 정격 및 성능",
        excerpt="응답시간, 소비전류, 중량 등 정격값을 명시합니다.",
        similarity=0.5,
    )

    pages = _card_meaning_pages([precaution_page, rating_page], RATING_PERFORMANCE_SECTION_SIGNALS)

    assert pages == ["rating-1"]


def test_card_meaning_pages_ignores_body_text_and_partial_title_matches() -> None:
    # 실제 문서에서 관찰된 문제: 어떤 섹션 제목은 "정격"/"성능" 중 한 단어만(다른 맥락으로)
    # 포함하기도 하고, 본문에는 있지만 제목엔 둘 다 없는 경우도 있다. 이런 부분적/본문
    # 일치는 카드의 진짜 의미(정격 및 성능 챕터)가 아니므로 절대 채택하면 안 된다 —
    # 무관한 페이지를 보여주느니 아예 빈 채로 두는 게 낫다(폴백 없음).
    body_only_page = ChatSource(
        document_id="doc-x",
        chunk_id="body-1",
        title="설비 매뉴얼",
        source_type="equipment_manual",
        section="2.6.1 손가락 검출",  # 제목엔 "정격"/"성능" 둘 다 없음
        excerpt="본 제품의 정격 전압은 24V이며 성능 기준을 만족합니다.",
        similarity=0.5,
    )
    partial_title_page = ChatSource(
        document_id="doc-x",
        chunk_id="partial-1",
        title="설비 매뉴얼",
        source_type="equipment_manual",
        # "성능"만 있고 "정격"은 없음 — 정격/성능 챕터가 아니라 다른 맥락의 언급.
        section="· 허용 광축 설정에 따른 검출 성능 변화",
        excerpt="설정 항목별 변경 내용을 정리합니다.",
        similarity=0.5,
    )

    pages = _card_meaning_pages(
        [body_only_page, partial_title_page],
        RATING_PERFORMANCE_SECTION_SIGNALS,
    )

    assert pages == []


def test_finalize_maintenance_answer_fills_rating_performance_pages_independent_of_manual_steps() -> None:
    narrow_source = ChatSource(
        document_id="doc-x",
        chunk_id="narrow-1",
        title="설비 매뉴얼",
        source_type="equipment_manual",
        section="안전을 위한 주의사항",
        excerpt="작업 전 위험구역 출입을 통제하십시오.",
        similarity=0.9,
    )
    full_doc_rating_source = ChatSource(
        document_id="doc-x",
        chunk_id="full-doc-1",
        title="설비 매뉴얼",
        source_type="equipment_manual",
        section="2. 정격 및 성능",
        excerpt="응답시간, 소비전류, 중량 등 정격값을 명시합니다.",
        similarity=0.0,
    )
    # manual_steps가 비어 있어도(이번 질문에선 절차 문장이 하나도 안 만들어졌어도)
    # 정격/성능 카드는 독립적으로 채워져야 한다.
    answer = MaintenanceAnswerDetails(
        summary=MaintenanceSummary(
            status="안전관리자 확인 필요",
            risk_level="판단 불가",
            risk_basis=[],
            core_warning="작업 전 확인 필요",
        ),
        manual_steps=[],
        evidence_chunk_ids=["narrow-1"],
    )

    finalized = finalize_maintenance_answer(
        answer,
        sources=[narrow_source],
        question="설비 정격 알려줘",
        full_document_sources=[narrow_source, full_doc_rating_source],
    )

    assert isinstance(finalized, MaintenanceAnswerDetails)
    assert finalized.manual_steps == []
    assert finalized.rating_performance_page_source_ids == ["full-doc-1"]


@pytest.mark.parametrize(
    ("source_text", "expected_terms", "excluded_terms"),
    [
        (
            "카메라 렌즈의 오염과 손상 여부를 확인하십시오.",
            ("카메라", "렌즈", "오염"),
            ("베어링", "벨트"),
        ),
        (
            "베어링 설치 전 축과 하우징의 손상 및 치수를 점검하십시오.",
            ("베어링", "축", "하우징"),
            ("렌즈", "벨트"),
        ),
        (
            "컨베이어 벨트의 장력과 풀리 정렬 상태를 확인하십시오.",
            ("벨트", "장력", "풀리"),
            ("렌즈", "베어링"),
        ),
        (
            "배관 연결부의 압력과 누설 여부를 점검하십시오.",
            ("압력", "누설"),
            ("광축", "벨트"),
        ),
    ],
)
def test_pre_checks_preserve_product_specific_manual_terms(
    source_text: str,
    expected_terms: tuple[str, ...],
    excluded_terms: tuple[str, ...],
) -> None:
    phrase = _pre_check_phrase(source_text)

    assert all(term in phrase for term in expected_terms)
    assert all(term not in phrase for term in excluded_terms)
    assert phrase.endswith(("확인합니다", "점검합니다"))


def test_post_install_product_instruction_is_not_used_as_a_pre_check() -> None:
    assert (
        _pre_check_phrase(
            "설치 후 베어링의 이상음과 과열 여부를 확인하십시오."
        )
        == ""
    )


def test_document_summary_does_not_return_unverified_information() -> None:
    source = ChatSource(
        document_id="doc-summary",
        chunk_id="summary-1",
        title="장비 설치 매뉴얼",
        source_type="equipment_manual",
        excerpt="장비의 설치 조건과 점검 절차를 설명합니다.",
        similarity=0.9,
    )

    details = source_based_fallback(
        "document_qa",
        [source],
        question="선택한 매뉴얼을 요약해줘.",
    )

    assert isinstance(details, DocumentAnswerDetails)
    assert details.unverified_information == []


def test_document_model_name_ignores_manual_filename_prefix() -> None:
    source = ChatSource(
        document_id="sfl-manual",
        chunk_id="sfl-summary",
        title="SFL 라이트 커튼 매뉴얼",
        original_filename="MSO-SFL_A_U1-V3.1-KO_20250912_W.pdf",
        source_type="equipment_manual",
        excerpt="SFL 시리즈의 설치 및 배선 조건을 설명합니다.",
        similarity=0.9,
        document_profile={"model_names": ["MSO-SFL"]},
    )

    details = source_based_fallback(
        "document_qa",
        [source],
        question="라이트 커튼 매뉴얼을 요약해줘.",
    )

    assert isinstance(details, DocumentAnswerDetails)
    assert details.overview.model_name == "SFL 시리즈"


def test_document_model_name_uses_series_before_locale_code() -> None:
    source = ChatSource(
        document_id="vg-manual",
        chunk_id="vg-summary",
        title="VG 비전 센서 제품 매뉴얼",
        original_filename="VG_KO_TCD210213AF_20241113_MANUAL_W.pdf",
        source_type="equipment_manual",
        excerpt="VG Series 비전 센서의 주요 특징과 설치 조건을 설명합니다.",
        similarity=0.9,
        document_profile={"model_names": ["미지정 모델"]},
    )

    details = source_based_fallback(
        "document_qa",
        [source],
        question="VG 센서 매뉴얼을 요약해줘.",
    )

    assert isinstance(details, DocumentAnswerDetails)
    assert details.overview.model_name == "VG 시리즈"


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("차단하여 광학 성능 향상 렌즈 커버", "렌즈 커버"),
        ("FTP 서버로 데이터 저장 비전센서", "비전센서"),
        ("원자력 제어 장치", ""),
        ("연소장치", ""),
        ("안전장치", ""),
        ("방범/방재장치", ""),
        ("반드시 2중으로 안전장치", ""),
        ("SELV 전원 장치", "안전 초저전압(SELV) 전원 공급 장치"),
        ("오동작을 방지하기 위해 전원 I/O 케이블", "전원·I/O 케이블"),
        ("1-KO_20250912_W 라이트 커튼", "라이트 커튼"),
        ("광축", ""),
        ("기종 형태 L 라이트 커튼", "라이트 커튼"),
        ("미지정 제조사 미분류 설비", ""),
        ("조명 일체형 비전 센서", "조명 일체형 비전 센서"),
    ],
)
def test_document_profile_entities_are_clear_product_names(
    raw_value: str,
    expected: str,
) -> None:
    assert _document_entity_phrase(raw_value, known=True) == expected


def test_document_summary_filters_profile_descriptions_and_excluded_applications() -> None:
    source = ChatSource(
        document_id="vision-manual",
        chunk_id="vision-summary",
        title="VG 비전 센서 매뉴얼",
        source_type="component_manual",
        excerpt="비전 센서의 렌즈 커버와 데이터 저장 기능을 설명합니다.",
        similarity=0.9,
        document_profile={
            "product_names": ["조명 일체형 비전 센서"],
            "equipment": ["미지정 제조사 미분류 설비", "산업용 로봇"],
            "components": [
                "차단하여 광학 성능 향상 렌즈 커버",
                "FTP 서버로 데이터 저장 비전센서",
                "원자력 제어 장치",
                "연소장치",
                "안전장치",
                "방범/방재장치",
                "반드시 2중으로 안전장치",
                "SELV 전원 장치",
                "오동작을 방지하기 위해 전원 I/O 케이블",
                "1-KO_20250912_W 라이트 커튼",
                "광축",
                "기종 형태 L 라이트 커튼",
                "산업용 로봇 방호장치",
                "브라켓",
                "케이블",
                "SFL-LC 케이블",
            ],
        },
    )

    details = source_based_fallback(
        "document_qa",
        [source],
        question="VG 비전 센서 매뉴얼을 요약해줘.",
    )

    assert isinstance(details, DocumentAnswerDetails)
    assert details.related_equipment == ["산업용 로봇"]
    assert details.related_components == [
        "렌즈 커버",
        "비전센서",
        "안전 초저전압(SELV) 전원 공급 장치",
        "라이트 커튼",
        "브라켓",
        "케이블",
        "조명 일체형 비전 센서",
    ]


def test_pre_check_removes_ocr_list_number_and_rewrites_requirement_as_check() -> None:
    phrase = _pre_check_phrase(
        "1 기계의 위험 영역에서 SFL(A) 설치 위치까지의 거리는 "
        "계산된 안전거리와 같거나 그 이상으로 구성되어 있다"
    )

    assert not phrase.startswith("1 ")
    assert "안전거리" in phrase
    assert phrase.endswith("인지 확인합니다")


def test_pre_check_rewrites_installed_condition_as_check_action() -> None:
    phrase = _pre_check_phrase(
        "6 설치된 투광기와 수광기의 외형 구조를 확인하였을 때 "
        "흠집 또는 파손이 없는 상태로 구성되어 있다"
    )

    assert not phrase.startswith("6 ")
    assert "흠집 또는 파손" in phrase
    assert phrase.endswith("없는지 확인합니다")


def test_manufacturer_specific_prechecks_keep_each_manual_wording() -> None:
    manufacturer_a = ChatSource(
        document_id="bearing-maker-a",
        chunk_id="maker-a-fit",
        title="A사 베어링 설치 매뉴얼",
        source_type="component_manual",
        excerpt="베어링 설치 전에 축 지름 50 mm와 공차 h6 충족 여부를 확인하십시오.",
        similarity=0.9,
    )
    manufacturer_b = ChatSource(
        document_id="bearing-maker-b",
        chunk_id="maker-b-fit",
        title="B사 베어링 설치 매뉴얼",
        source_type="component_manual",
        excerpt="조립 전에 하우징 내경의 긁힘과 타원 변형 상태를 점검하십시오.",
        similarity=0.9,
    )

    answer_a = source_based_fallback(
        "maintenance_guide",
        [manufacturer_a],
        question="A사 베어링 설치 전 필수사항을 알려줘.",
    )
    answer_b = source_based_fallback(
        "maintenance_guide",
        [manufacturer_b],
        question="B사 베어링 설치 전 필수사항을 알려줘.",
    )

    assert isinstance(answer_a, MaintenanceAnswerDetails)
    assert isinstance(answer_b, MaintenanceAnswerDetails)
    assert [item.content for item in answer_a.pre_checks] == [
        "베어링 설치 전에 축 지름 50 mm와 공차 h6 충족 여부를 확인합니다"
    ]
    assert [item.content for item in answer_b.pre_checks] == [
        "조립 전에 하우징 내경의 긁힘과 타원 변형 상태를 점검합니다"
    ]
    assert answer_a.pre_checks != answer_b.pre_checks


@pytest.mark.parametrize(
    ("source_text", "expected"),
    [
        (
            "제품과 기계의 위험부 사이에는 반드시 안전 거리를 확보하십시오.",
            "제품과 기계의 위험부 사이에는 반드시 안전 거리를 확보하십시오.",
        ),
        (
            "제품 설치 시 투광기와 수광기의 상단 및 하단 광축 표시등을 정확히 일치시키십시오.",
            "제품 설치 시 투광기와 수광기의 상단 및 하단 광축 표시등을 정확히 일치시키십시오.",
        ),
        (
            "제품을 여러 세트로 사용하는 경우 상호 간섭이 발생하지 않도록 배치하거나 차광판을 사용하십시오.",
            "제품을 여러 세트로 사용하는 경우 상호 간섭이 발생하지 않도록 배치하거나 차광판을 사용하십시오.",
        ),
        (
            "강한 외란광 또는 광택면의 반사광이 수광기로 직접 입사되지 않는 장소에 설치하십시오.",
            "강한 외란광 또는 광택면의 반사광이 수광기로 직접 입사되지 않는 장소에 설치하십시오.",
        ),
    ],
)
def test_light_curtain_manual_steps_keep_device_specific_details(
    source_text: str,
    expected: str,
) -> None:
    assert _manual_step_phrase(source_text) == expected


def test_light_curtain_fallback_keeps_multiple_installation_details() -> None:
    excerpts = [
        "제품과 기계의 위험부 사이에는 반드시 안전 거리를 확보하십시오.",
        "제품 설치 시 투광기와 수광기의 상단 및 하단 광축 표시등을 정확히 일치시키십시오.",
        "제품을 여러 세트로 사용하는 경우 상호 간섭이 발생하지 않도록 배치하거나 차광판을 사용하십시오.",
        "강한 외란광 또는 광택면의 반사광이 수광기로 직접 입사되지 않는 장소에 설치하십시오.",
        "설치 후 검출 영역을 차단했을 때 제어출력이 정지되는지 확인하십시오.",
    ]
    sources = [
        ChatSource(
            document_id="doc-light",
            chunk_id=f"light-install-{index}",
            title="라이트커튼 매뉴얼",
            source_type="equipment_manual",
            section="설치 주의사항",
            excerpt=excerpt,
            similarity=0.9,
        )
        for index, excerpt in enumerate(excerpts)
    ]

    details = source_based_fallback(
        "maintenance_guide",
        sources,
        question="라이트커튼 설치 시 주의사항을 알려줘.",
    )

    assert isinstance(details, MaintenanceAnswerDetails)
    assert len(details.manual_steps) == 5
    assert any("안전 거리" in item.content for item in details.manual_steps)
    assert any("광축 표시등" in item.content for item in details.manual_steps)
    assert any("상호 간섭" in item.content for item in details.manual_steps)
    assert any(
        "외란광" in item.content and "반사광" in item.content
        for item in details.manual_steps
    )
    assert {
        chunk_id
        for item in details.manual_steps
        for chunk_id in item.evidence_chunk_ids
    } == {f"light-install-{index}" for index in range(5)}
    checklist = source_based_checklist_items(
        "maintenance_guide",
        sources,
        question="라이트커튼 설치 시 주의사항을 알려줘.",
    )
    assert 3 <= len(checklist) <= 5
    checklist_text = " ".join(item.content for item in checklist)
    assert "정렬" in checklist_text
    assert "간섭" in checklist_text
    assert "안전거리" in checklist_text
    assert "정상 반응" in checklist_text
    assert all(item.evidence_chunk_ids for item in checklist)


def test_bearing_tbm_uses_bearing_specific_items_and_stays_within_five() -> None:
    excerpts = [
        "베어링 설치 전 축과 하우징의 손상 및 치수를 확인하십시오.",
        "지정된 윤활제를 정량 주입하십시오.",
        "베어링과 축의 정렬 상태를 확인하십시오.",
        "조립 후 베어링의 이상음과 과열 여부를 점검하십시오.",
        "회전부가 움직이지 않도록 전원을 차단하십시오.",
        "작업 책임자에게 작업 내용을 보고하십시오.",
    ]
    sources = [
        ChatSource(
            document_id="doc-bearing",
            chunk_id=f"bearing-{index}",
            title="베어링 설치 매뉴얼",
            source_type="equipment_manual",
            section="설치 점검",
            excerpt=excerpt,
            similarity=0.9,
        )
        for index, excerpt in enumerate(excerpts)
    ]

    checklist = source_based_checklist_items(
        "maintenance_guide",
        sources,
        question="베어링 설치 시 확인사항을 알려줘.",
    )

    assert len(checklist) == 5
    contents = [item.content for item in checklist]
    assert any("오염·손상" in content for content in contents)
    assert any("윤활" in content for content in contents)
    assert any("정렬" in content for content in contents)
    assert any("전원" in content and "차단" in content for content in contents)
    assert all("광축" not in content for content in contents)
    assert all(item.evidence_chunk_ids for item in checklist)


def test_light_curtain_pre_checks_prioritize_three_installation_essentials() -> None:
    excerpts = [
        "제품과 기계의 위험부 사이에는 반드시 안전 거리를 확보하십시오.",
        (
            "기계의 위험부에 접근하기 위해서는 반드시 인체가 검출 영역을 통과하는 "
            "구조로 설치하고 우회가 가능하면 별도의 가드를 설치하십시오."
        ),
        "제품 설치 시 투광기와 수광기의 상단 및 하단 광축 표시등을 정확히 일치시키십시오.",
        "제품을 여러 세트로 사용하는 경우 상호 간섭이 발생하지 않도록 배치하거나 차광판을 사용하십시오.",
        "강한 외란광 또는 광택면의 반사광이 수광기로 직접 입사되지 않는 장소에 설치하십시오.",
    ]
    sources = [
        ChatSource(
            document_id="doc-light",
            chunk_id=f"light-precheck-{index}",
            title="라이트커튼 매뉴얼",
            source_type="equipment_manual",
            section="설치 주의사항",
            excerpt=excerpt,
            similarity=0.9,
        )
        for index, excerpt in enumerate(excerpts)
    ]

    details = source_based_fallback(
        "maintenance_guide",
        sources,
        question="라이트커튼 설치 시 작업 전 필수사항을 알려줘.",
    )

    assert isinstance(details, MaintenanceAnswerDetails)
    assert [item.evidence_chunk_ids for item in details.pre_checks] == [
        ["light-precheck-0"],
        ["light-precheck-2"],
        ["light-precheck-4"],
    ]
    assert [item.content for item in details.pre_checks] == [
        excerpts[0].rstrip("."),
        excerpts[2].rstrip("."),
        excerpts[4].rstrip("."),
    ]


def test_enrichment_prefers_specific_manual_pre_checks_over_generic_model_text() -> None:
    fallback = MaintenanceAnswerDetails(
        summary=MaintenanceSummary(
            status="안전관리자 확인 필요",
            risk_level="판단 불가",
            risk_basis=[],
            core_warning="안전거리 확인 필요",
        ),
        pre_checks=[
            EvidenceBackedItem(
                content="기계 위험부와 라이트커튼 사이의 안전거리 확보 여부 확인",
                evidence_chunk_ids=["light-1"],
            )
        ],
        evidence_chunk_ids=["light-1"],
    )
    model_answer = MaintenanceAnswerDetails(
        summary=fallback.summary,
        pre_checks=[
            EvidenceBackedItem(
                content="방호장치 설치 위치와 고정 상태 확인",
                evidence_chunk_ids=["light-1"],
            )
        ],
        evidence_chunk_ids=["light-1"],
    )

    enriched = enriched_structured_answer(
        model_answer,
        fallback,
        expected_type="maintenance_guide",
    )

    assert isinstance(enriched, MaintenanceAnswerDetails)
    assert enriched.pre_checks == fallback.pre_checks


def test_enrichment_does_not_use_model_prechecks_without_manual_evidence() -> None:
    summary = MaintenanceSummary(
        status="안전관리자 확인 필요",
        risk_level="판단 불가",
        risk_basis=[],
        core_warning="근거 확인 필요",
    )
    fallback = MaintenanceAnswerDetails(summary=summary, pre_checks=[])
    model_answer = MaintenanceAnswerDetails(
        summary=summary,
        pre_checks=[
            EvidenceBackedItem(
                content="장착 위치와 고정 상태 확인",
                evidence_chunk_ids=["manual-1"],
            )
        ],
    )

    enriched = enriched_structured_answer(
        model_answer,
        fallback,
        expected_type="maintenance_guide",
    )

    assert isinstance(enriched, MaintenanceAnswerDetails)
    assert enriched.pre_checks == []


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("비전에 대해 알려줄래?", "비전"),
        ("비전에 대해 설명해 주세요.", "비전"),
        ("라이트커튼을 알려주세요.", "라이트커튼"),
    ],
)
def test_question_subject_excludes_request_phrases(
    question: str,
    expected: str,
) -> None:
    assert _question_subject_phrase(question) == expected


def test_component_summary_does_not_echo_the_question_as_its_subject() -> None:
    source = ChatSource(
        document_id="doc-vision",
        chunk_id="vision-role",
        title="비전 센서 사용자 매뉴얼",
        source_type="equipment_manual",
        excerpt="비전 센서는 기능 설정 및 상태 모니터링을 지원합니다.",
        similarity=0.9,
    )

    details = source_based_fallback(
        "component_info",
        [source],
        question="비전에 대해 알려줄래?",
    )

    assert isinstance(details, ComponentAnswerDetails)
    assert details.one_line_description.startswith("비전은")
    assert "대해" not in details.one_line_description
    assert "알려줄래" not in details.one_line_description


@pytest.mark.parametrize(
    "text",
    [
        "강한 자기력 및 고주파 노이즈가 발생하는 기기 근처에서는 사용하지 마십시오.",
        "광 간섭이 발생하는 장소에는 설치하지 마십시오.",
        "전자기 노이즈가 강한 위치는 피하십시오.",
    ],
)
def test_component_usage_rejects_interference_avoidance_conditions(text: str) -> None:
    assert _component_usage_phrase(text) == ""


def test_component_usage_keeps_positive_mounting_location() -> None:
    assert (
        _component_usage_phrase("브라켓에 단단히 장착하여 사용합니다.")
        == "고정·체결이 필요한 장착 위치"
    )


def test_component_fallback_moves_noise_avoidance_to_precautions() -> None:
    source = ChatSource(
        document_id="doc-vision",
        chunk_id="vision-warning",
        title="비전 센서 매뉴얼",
        source_type="equipment_manual",
        section="취급상의 주의",
        excerpt="강한 자기력 및 고주파 노이즈가 발생하는 기기 근처에서는 사용하지 마십시오.",
        similarity=0.9,
    )

    details = source_based_fallback(
        "component_info",
        [source],
        question="비전에 대해 요약해줄래?",
    )

    assert isinstance(details, ComponentAnswerDetails)
    assert details.usage_locations == []
    assert [item.content for item in details.precautions] == [
        "강한 자기장·고주파 노이즈 발생 기기 근처에서 사용하지 않기"
    ]


def _source(chunk_id: str, source_type: str) -> ChatSource:
    return ChatSource(
        document_id=f"doc-{chunk_id}",
        chunk_id=chunk_id,
        title="verified source",
        source_type=source_type,
        excerpt="verified excerpt",
        similarity=0.8,
    )


def test_document_fallback_never_contains_tbm_fields() -> None:
    details = source_based_fallback("document_qa", [_source("manual-1", "component_manual")])

    assert details is not None
    assert details.answer_type == "document_qa"
    assert "checklist" not in details.model_dump()
    assert "hazards" not in details.model_dump()


def test_component_fallback_never_contains_maintenance_steps() -> None:
    details = source_based_fallback("component_info", [_source("manual-1", "component_manual")])

    assert details is not None
    assert details.answer_type == "component_info"
    assert "manual_steps" not in details.model_dump()


def test_public_maintenance_fallback_does_not_fill_manual_prechecks() -> None:
    source = ChatSource(
        document_id="doc-public",
        chunk_id="public-1",
        title="프레스 금형작업의 안전에 관한 기술지침",
        source_type="public_guide",
        document_scope="public",
        excerpt=(
            "프레스 작업 전 Lockout/Tagout 절차를 따른다. "
            "아무도 위험 지역에 들어가지 못하도록 확인한다. "
            "프레스 주변에는 미끄럼 방지 조치를 하고 청소를 우선적으로 한다."
        ),
        similarity=0.8,
    )

    details = source_based_fallback("maintenance_guide", [source])
    checklist = source_based_checklist_items("maintenance_guide", [source])

    assert details is not None
    assert details.answer_type == "maintenance_guide"
    assert details.pre_checks == []
    assert details.hazards
    assert details.manual_steps == []
    assert details.related_regulations_and_incidents
    assert details.summary.risk_level == "판단 불가"
    assert details.summary.risk_basis == []
    assert "예상 핵심 위험" in details.summary.core_warning
    assert checklist
    assert all(item.content.endswith("하기") for item in checklist)
    assert "[자료유형]" not in details.related_regulations_and_incidents[0].content
    assert details.related_regulations_and_incidents[0].content.startswith("[안전 가이드]")


def test_public_guides_do_not_become_manufacturer_prechecks() -> None:
    def conveyor_source(chunk_id: str, excerpt: str) -> ChatSource:
        return ChatSource(
            document_id=f"doc-{chunk_id}",
            chunk_id=chunk_id,
            title="컨베이어 안전작업 지침",
            source_type="public_guide",
            document_scope="public",
            excerpt=excerpt,
            similarity=0.8,
        )

    sources = [
        conveyor_source(
            "guide-stop",
            "컨베이어 청소 작업 전에는 운전을 정지하고 작동하지 않도록 잠금장치를 설치한다.",
        ),
        conveyor_source(
            "guide-lock",
            "청소 전 전원 차단 후 잠금 상태를 확인한다.",
        ),
        conveyor_source(
            "guide-emergency",
            "비상정지 스위치는 작업현장에서 쉽게 접근할 수 있는 위치인지 점검한다.",
        ),
        conveyor_source(
            "guide-periodic",
            "정기적으로 정비하고 작업표준 및 취급요령을 교육한다.",
        ),
    ]

    details = source_based_fallback(
        "maintenance_guide",
        sources,
        question="컨베이어 청소할 때 작업 전 확인사항 알려줘",
    )
    checklist = source_based_checklist_items(
        "maintenance_guide",
        sources,
        question="컨베이어 청소할 때 작업 전 확인사항 알려줘",
    )

    assert isinstance(details, MaintenanceAnswerDetails)
    assert details.pre_checks == []
    assert checklist
    assert all(item.evidence_chunk_ids for item in checklist)
    assert details.precautions
    assert all(
        item.content.endswith(("하기", "않기", "금지"))
        for item in details.precautions
    )
    assert details.stop_conditions
    assert all("중지" in item.content for item in details.stop_conditions)


def test_maintenance_sections_have_distinct_styles_for_light_curtain() -> None:
    source = ChatSource(
        document_id="doc-light",
        chunk_id="light-1",
        title="라이트 커튼 사용자 매뉴얼",
        source_type="equipment_manual",
        document_scope="company",
        excerpt=(
            "광전자식 방호장치는 안전거리를 유지하여 설치해야 한다. "
            "설치 후 광축을 차단했을 때 위험한 동작이 멈추는지 확인한다. "
            "투광부와 수광부 정렬이 맞지 않거나 오검출이 발생하면 작업을 중지한다. "
            "방호장치를 임의로 우회하거나 무효화하지 않는다."
        ),
        similarity=0.8,
    )

    details = source_based_fallback(
        "maintenance_guide",
        [source],
        question="라이트 커튼 설치할 거야",
    )
    checklist = source_based_checklist_items(
        "maintenance_guide",
        [source],
        question="라이트 커튼 설치할 거야",
    )

    assert isinstance(details, MaintenanceAnswerDetails)
    pre_checks = [item.content for item in details.pre_checks]
    precautions = [item.content for item in details.precautions]
    stop_conditions = [item.content for item in details.stop_conditions]
    checklist_items = [item.content for item in checklist]

    assert "광전자식 방호장치는 안전거리를 유지하여 설치해야 한다" in pre_checks
    assert all(
        item.endswith(("하기", "맞추기", "않기"))
        for item in checklist_items
    )
    assert any(item.endswith("않기") for item in precautions)
    assert all("중지" in item for item in stop_conditions)
    assert "광전자식 방호장치는 안전거리를 유지하여 설치해야 한다" not in stop_conditions
    assert set(pre_checks).isdisjoint(checklist_items)
    assert set(pre_checks).isdisjoint(precautions)
    assert set(pre_checks).isdisjoint(stop_conditions)


def test_public_reference_items_do_not_expose_raw_dataset_prefix() -> None:
    source = ChatSource(
        document_id="doc-public",
        chunk_id="public-1",
        title="일반 안전작업 지침",
        source_type="public_guide",
        document_scope="public",
        excerpt=(
            "[자료유형] KOSHA GUIDE [제목] 일반 안전작업 지침 [내용] "
            "작업 전에는 위험요인을 확인하고 필요한 보호조치를 적용한다."
        ),
        similarity=0.8,
    )

    details = source_based_fallback("maintenance_guide", [source])

    assert details is not None
    assert details.related_regulations_and_incidents
    assert "[자료유형]" not in details.related_regulations_and_incidents[0].content
    assert "원문" not in details.related_regulations_and_incidents[0].content


def test_public_incident_fallback_does_not_convert_incident_text_into_procedure() -> None:
    source = ChatSource(
        document_id="doc-incident",
        chunk_id="incident-1",
        title="설비 내부 이물질 제거 중 협착",
        source_type="public_incident",
        document_scope="public",
        excerpt=(
            "【대책】 기계의 정비, 청소, 검사 등의 작업시 당해 기계의 "
            "운전을 정지시키고 표지판 부착, 시건장치 설치, 작업지휘자를 "
            "배치하여야 함. 비상정지장치를 설치하고 방호울을 설치하여야 함."
        ),
        similarity=0.8,
    )

    details = source_based_fallback("maintenance_guide", [source])
    checklist = source_based_checklist_items("maintenance_guide", [source])

    assert isinstance(details, MaintenanceAnswerDetails)
    dumped = json.dumps(details.model_dump(mode="json"), ensure_ascii=False)
    assert "【대책】" not in dumped
    assert details.pre_checks == []
    assert details.hazards
    assert details.stop_conditions == []
    assert details.related_regulations_and_incidents
    assert checklist == []


def test_public_incident_removes_trailing_ocr_page_number() -> None:
    source = ChatSource(
        document_id="incident-conveyor",
        chunk_id="incident-conveyor-number",
        title="컨베이어 이송 중 협착사고",
        source_type="public_incident",
        document_scope="public",
        excerpt=(
            "비상정지 스위치가 운전실에만 설치되어 작업자가 긴급상황 발생 시 "
            "전원을 차단할 수 없는 상태였음.4입니다."
        ),
        similarity=0.9,
    )

    details = source_based_fallback(
        "maintenance_guide",
        [source],
        question="컨베이어 벨트를 교체해야 해.",
    )

    assert isinstance(details, MaintenanceAnswerDetails)
    dumped = json.dumps(details.model_dump(mode="json"), ensure_ascii=False)
    assert "4입니다" not in dumped
    cleaned = _clean_source_excerpt(source.excerpt)
    assert cleaned.endswith("상태였음.")
    assert _polite_document_sentence(cleaned) == (
        "비상정지 스위치가 운전실에만 설치되어 작업자가 긴급상황 발생 시 "
        "전원을 차단할 수 없는 상태였습니다."
    )


def test_unrelated_public_references_are_omitted_from_maintenance_answer() -> None:
    sources = [
        ChatSource(
            document_id="guide-light",
            chunk_id="guide-light",
            title="광전자식 방호장치 교체 안전 가이드",
            source_type="public_guide",
            document_scope="public",
            excerpt="광전자식 방호장치 교체 전 전원을 차단한다.",
            similarity=0.8,
        ),
        ChatSource(
            document_id="law-crane",
            chunk_id="law-crane",
            title="이동식 크레인 안전 기준",
            source_type="public_law",
            document_scope="public",
            excerpt="크레인 와이어로프와 훅의 상태를 점검한다.",
            similarity=0.9,
        ),
        ChatSource(
            document_id="incident-press",
            chunk_id="incident-press",
            title="프레스 금형 교체 사고사례",
            source_type="public_incident",
            document_scope="public",
            excerpt="프레스 금형 교체 중 협착 사고가 발생했다.",
            similarity=0.9,
        ),
    ]

    details = source_based_fallback(
        "maintenance_guide",
        sources,
        question="라이트커튼을 교체하려면 어떻게 해야 해?",
    )

    assert isinstance(details, MaintenanceAnswerDetails)
    assert [
        item.evidence_chunk_ids
        for item in details.related_regulations_and_incidents
    ] == [["guide-light"]]


def test_conveyor_belt_question_keeps_reversed_conveyor_incident_title() -> None:
    incident = ChatSource(
        document_id="incident-conveyor",
        chunk_id="incident-conveyor",
        title="벨트컨베이어에 협착",
        source_type="public_incident",
        document_scope="public",
        excerpt="벨트컨베이어 정비 작업 중 운전을 정지하지 않아 협착 사고가 발생했다.",
        similarity=0.9,
    )
    unrelated_subtype = ChatSource(
        document_id="incident-screw-conveyor",
        chunk_id="incident-screw-conveyor",
        title="스크류컨베이어 정비 중 끼임",
        source_type="public_incident",
        document_scope="public",
        excerpt="스크류컨베이어 정비 작업 중 끼임 사고가 발생했다.",
        similarity=0.95,
    )

    details = source_based_fallback(
        "maintenance_guide",
        [incident, unrelated_subtype],
        question="컨베이어 벨트를 교체해야 해.",
    )

    assert isinstance(details, MaintenanceAnswerDetails)
    assert [
        item.evidence_chunk_ids
        for item in details.related_regulations_and_incidents
    ] == [["incident-conveyor"]]


def test_maintenance_fallback_does_not_invent_press_for_light_curtain_source() -> None:
    source = ChatSource(
        document_id="doc-light",
        chunk_id="light-1",
        title="라이트 커튼 사용자 매뉴얼",
        source_type="equipment_manual",
        document_scope="company",
        section="안전을 위한 주의사항",
        excerpt=(
            "라이트 커튼 설치 시 PC 설정 툴로 기능을 설정한 후 의도한 대로 "
            "동작하는지 확인한다. 인터락 해제 전 위험구역 내 작업자가 없는지 "
            "확인하고, 작업대와 통로 상태를 점검한다."
        ),
        similarity=0.8,
    )

    details = source_based_fallback("maintenance_guide", [source])

    assert details is not None
    dumped = json.dumps(details.model_dump(mode="json"), ensure_ascii=False)
    assert "프레스" not in dumped
    assert "청소 작업" not in dumped
    assert details.pre_checks
    assert details.hazards
    assert details.manual_steps
    assert details.summary.risk_level == "판단 불가"


def test_document_fallback_populates_related_fields_from_evidence() -> None:
    source = ChatSource(
        document_id="doc-light",
        chunk_id="light-1",
        title="라이트 커튼 사용자 매뉴얼",
        source_type="equipment_manual",
        section="모델 구성",
        excerpt=(
            "SF L 라이트 커튼(Light curtain)은 세이프티 컴포넌트이며 "
            "광축 수, PC 설정 툴, 설치 및 점검 항목을 포함한다."
        ),
        similarity=0.8,
    )

    details = source_based_fallback("document_qa", [source])

    assert isinstance(details, DocumentAnswerDetails)
    assert details.overview.document_type == "PDF / 장비 매뉴얼"
    assert details.main_contents
    assert details.main_contents[0].evidence_chunk_ids == ["light-1"]
    assert details.unverified_information == []
    assert "설치·장착·배선" in details.main_contents[0].content
    assert details.related_equipment == []
    assert "라이트 커튼" in details.related_components
    assert "설정 툴" in details.related_components
    assert all(
        "확인" not in component and "점검" not in component
        for component in details.related_components
    )
    assert details.supported_tasks
    assert all("확인" in task or "점검" in task for task in details.supported_tasks)


def test_component_fallback_populates_sections_from_evidence() -> None:
    source = ChatSource(
        document_id="doc-light",
        chunk_id="light-1",
        title="라이트 커튼 사용자 매뉴얼",
        source_type="equipment_manual",
        section="안전을 위한 주의사항",
        excerpt=(
            "라이트 커튼은 위험구역 접근과 광축 차단을 검출한다. "
            "PC 설정 툴로 기능을 변경한 후 의도한 대로 동작하는지 확인하고, "
            "인터락 상태 해제 후 기계 재기동 전 위험 구역 내 작업자가 없는지 확인한다."
        ),
        similarity=0.8,
    )

    details = source_based_fallback("component_info", [source])

    assert isinstance(details, ComponentAnswerDetails)
    assert details.one_line_description.startswith("라이트 커튼은")
    assert details.main_roles
    assert details.main_roles[0].evidence_chunk_ids == ["light-1"]
    assert all("확인" not in item.content for item in details.main_roles)
    assert details.usage_locations
    assert details.precautions
    assert all(item.content.endswith("않기") for item in details.precautions)


def test_empty_qwen_component_sections_are_backfilled_from_verified_candidates() -> None:
    fallback = source_based_fallback(
        "component_info",
        [
            ChatSource(
                document_id="doc-light",
                chunk_id="light-1",
                title="라이트 커튼 사용자 매뉴얼",
                source_type="equipment_manual",
                section="안전을 위한 주의사항",
                excerpt=(
                    "라이트 커튼은 위험구역 접근과 광축 차단을 검출한다. "
                    "기능 설정 후 의도한 대로 동작하는지 확인한다."
                ),
                similarity=0.8,
            )
        ],
    )
    qwen_details = ComponentAnswerDetails(
        one_line_description="PC 설정 툴을 통해 설정 및 변경이 가능한 안전 장치입니다.",
        main_roles=[],
        usage_locations=[],
        precautions=[],
        evidence_chunk_ids=["light-1"],
    )

    enriched = enriched_structured_answer(
        qwen_details,
        fallback,
        expected_type="component_info",
    )

    assert isinstance(enriched, ComponentAnswerDetails)
    assert enriched.one_line_description == qwen_details.one_line_description
    assert enriched.main_roles
    assert enriched.precautions


def test_empty_qwen_document_sections_are_backfilled_from_verified_candidates() -> None:
    sources = [
        ChatSource(
            document_id="doc-light",
            chunk_id="light-1",
            title="라이트 커튼 사용자 매뉴얼",
            source_type="equipment_manual",
            section="모델 구성",
            excerpt="라이트 커튼은 세이프티 컴포넌트이며 설치, 점검, PC 설정 항목을 포함한다.",
            similarity=0.8,
        )
    ]
    fallback = source_based_fallback(
        "document_qa",
        sources,
    )
    qwen_details = DocumentAnswerDetails(
        main_contents=[
            EvidenceBackedItem(
                content="라이트 커튼 모델 구성 설명",
                evidence_chunk_ids=["light-1"],
            )
        ],
        related_equipment=[],
        related_components=[],
        supported_tasks=[],
        evidence_chunk_ids=["light-1"],
        unverified_information=[],
    )

    enriched = enriched_structured_answer(
        qwen_details,
        fallback,
        expected_type="document_qa",
    )

    assert isinstance(enriched, DocumentAnswerDetails)
    assert enriched.main_contents == qwen_details.main_contents
    assert enriched.related_equipment == []
    assert enriched.related_components
    assert enriched.supported_tasks

    finalized = finalize_document_answer(
        enriched,
        sources=sources,
        question="라이트 커튼 문서 요약해줘",
    )

    assert isinstance(finalized, DocumentAnswerDetails)
    assert finalized.main_contents != qwen_details.main_contents
    assert finalized.main_contents[0].content.endswith("합니다.")
    assert finalized.related_equipment == []
    assert "라이트 커튼" in finalized.related_components
    assert finalized.supported_tasks


def test_unretrieved_source_reference_drops_only_the_invalid_item() -> None:
    details = MaintenanceAnswerDetails(
        summary=MaintenanceSummary(
            status="안전관리자 확인 필요",
            risk_level="판단 불가",
            risk_basis=[],
            core_warning="작업 전 확인 필요",
        ),
        pre_checks=[
            EvidenceBackedItem(
                content="확인 항목",
                evidence_chunk_ids=["invented-chunk"],
            )
        ],
    )

    validated = validated_structured_answer(
        details,
        expected_type="maintenance_guide",
        sources=[_source("manual-1", "component_manual")],
    )

    assert isinstance(validated, MaintenanceAnswerDetails)
    assert validated.pre_checks == []


def test_manual_steps_require_manual_source() -> None:
    details = MaintenanceAnswerDetails(
        summary=MaintenanceSummary(
            status="안전관리자 확인 필요",
            risk_level="보통",
            risk_basis=[
                EvidenceBackedItem(
                    content="공용 안전자료",
                    evidence_chunk_ids=["guide-1"],
                )
            ],
            core_warning="작업 전 확인 필요",
        ),
        manual_steps=[
            EvidenceBackedItem(
                content="임의 절차",
                evidence_chunk_ids=["guide-1"],
            )
        ],
    )

    validated = validated_structured_answer(
        details,
        expected_type="maintenance_guide",
        sources=[_source("guide-1", "public_guide")],
    )

    assert validated is not None
    assert validated.manual_steps == []


def test_manual_steps_are_verified_and_kept_short_for_the_card_ui() -> None:
    details = MaintenanceAnswerDetails(
        summary=MaintenanceSummary(
            status="안전관리자 확인 필요",
            risk_level="판단 불가",
            risk_basis=[],
            core_warning="작업 전 확인 필요",
        ),
        manual_steps=[
            EvidenceBackedItem(
                content=(
                    "경고: 본 제품은 안전 관련 장치이며 설치, 배선, 점검, 유지보수, "
                    "주변 장치와의 연결 상태, 감지 영역, 안전거리, 현장 조건 등 여러 "
                    "조건을 모두 고려해야 합니다. 자세한 내용은 각 장의 설명과 표를 "
                    "참조하십시오. 임의 변경은 위험할 수 있습니다."
                ),
                evidence_chunk_ids=["manual-1"],
            ),
            EvidenceBackedItem(
                content="설치 전 광축 정렬 상태를 확인합니다.",
                evidence_chunk_ids=["manual-1"],
            ),
        ],
    )

    validated = validated_structured_answer(
        details,
        expected_type="maintenance_guide",
        sources=[_source("manual-1", "component_manual")],
    )

    assert validated is not None
    assert len(validated.manual_steps) == 2
    assert validated.manual_steps[0].content.startswith("경고: 본 제품")
    assert len(validated.manual_steps[0].content) <= 72
    assert validated.manual_steps[1].content == "설치 전 광축 정렬 상태를 확인합니다."


def test_question_irrelevant_manual_steps_are_removed() -> None:
    details = MaintenanceAnswerDetails(
        summary=MaintenanceSummary(
            status="안전관리자 확인 필요",
            risk_level="판단 불가",
            risk_basis=[],
            core_warning="작업 전 확인 필요",
        ),
        manual_steps=[
            EvidenceBackedItem(
                content="수광기 교체 후 설정 정보를 재전송합니다.",
                evidence_chunk_ids=["manual-1"],
            ),
            EvidenceBackedItem(
                content="위험구역 내 작업자 유무를 확인합니다.",
                evidence_chunk_ids=["manual-1"],
            ),
        ],
    )

    validated = validated_structured_answer(
        details,
        expected_type="maintenance_guide",
        sources=[_source("manual-1", "component_manual")],
        question="기계가 멈춰서 내부를 확인해야 해.",
    )

    assert validated is not None
    assert [step.content for step in validated.manual_steps] == [
        "위험구역 내 작업자 유무를 확인합니다."
    ]


def test_evidence_backed_component_items_require_verified_sources() -> None:
    from app.schemas.chat import ComponentAnswerDetails

    details = ComponentAnswerDetails(
        one_line_description="광축 차단을 감지하는 장치",
        main_roles=[
            EvidenceBackedItem(content="근거 없는 역할", evidence_chunk_ids=[]),
            EvidenceBackedItem(content="검증된 역할", evidence_chunk_ids=["manual-1"]),
        ],
        usage_locations=[],
        precautions=[],
        evidence_chunk_ids=["manual-1"],
    )

    validated = validated_structured_answer(
        details,
        expected_type="component_info",
        sources=[_source("manual-1", "component_manual")],
    )

    assert validated is not None
    assert [item.content for item in validated.main_roles] == ["검증된 역할"]


def test_component_items_are_rewritten_as_precaution_phrases() -> None:
    long_raw = (
        "PC 설정 툴을 사용하여 기능을 설정하거나 변경한 후에는 반드시 의도한 대로 "
        "동작하는지 확인하십시오. 제품이 의도한 대로 설정되지 않은 경우 인사사고 "
        "발생 위험이 있습니다. 구성 변경, 부품 교체, 광축 수 변경 등 여러 조건을 "
        "모두 확인해야 하며 자세한 내용은 제조사 매뉴얼 각 항목을 참조하십시오."
    )
    details = ComponentAnswerDetails(
        one_line_description="검색 근거에서 확인되는 안전 장치",
        main_roles=[
            EvidenceBackedItem(
                content="작업자 접근 여부를 검출합니다.",
                evidence_chunk_ids=["manual-1"],
            )
        ],
        usage_locations=[],
        precautions=[
            EvidenceBackedItem(content=long_raw, evidence_chunk_ids=["manual-1"]),
            EvidenceBackedItem(
                content="기능 변경 후 정상 동작 여부를 확인합니다.",
                evidence_chunk_ids=["manual-1"],
            ),
        ],
        evidence_chunk_ids=["manual-1"],
    )

    validated = validated_structured_answer(
        details,
        expected_type="component_info",
        sources=[_source("manual-1", "component_manual")],
    )

    assert validated is not None
    assert [item.content for item in validated.precautions] == [
        "검출부 정렬을 임의로 변경하지 않기",
        "정상 동작 시험 없이 사용하지 않기",
    ]


def test_company_policy_is_allowed_only_as_a_related_maintenance_reference() -> None:
    details = MaintenanceAnswerDetails(
        summary=MaintenanceSummary(
            status="안전관리자 확인 필요",
            risk_level="판단 불가",
            risk_basis=[],
            core_warning="작업 전 확인 필요",
        ),
        related_regulations_and_incidents=[
            EvidenceBackedItem(
                content="회사의 승인된 작업 기준",
                evidence_chunk_ids=["policy-1"],
            ),
            EvidenceBackedItem(
                content="매뉴얼 항목을 회사 규정으로 잘못 표시",
                evidence_chunk_ids=["manual-1"],
            ),
        ],
    )

    validated = validated_structured_answer(
        details,
        expected_type="maintenance_guide",
        sources=[
            _source("policy-1", "company_policy"),
            _source("manual-1", "component_manual"),
        ],
    )

    assert isinstance(validated, MaintenanceAnswerDetails)
    assert [
        item.evidence_chunk_ids
        for item in validated.related_regulations_and_incidents
    ] == [["policy-1"]]


def test_incident_source_cannot_create_tbm_checklist_item() -> None:
    validated = validated_checklist_items(
        [
            ChatChecklistItem(
                content="사고사례를 제조사 절차처럼 사용",
                sequence=1,
                evidence_chunk_ids=["incident-1"],
            )
        ],
        sources=[_source("incident-1", "public_incident")],
    )

    assert validated == []


def test_risk_level_falls_back_when_risk_basis_has_no_verified_source() -> None:
    details = MaintenanceAnswerDetails(
        summary=MaintenanceSummary(
            status="안전관리자 확인 필요",
            risk_level="높음",
            risk_basis=[],
            core_warning="작업 전 확인 필요",
        ),
    )

    validated = validated_structured_answer(
        details,
        expected_type="maintenance_guide",
        sources=[_source("manual-1", "component_manual")],
    )

    assert validated is not None
    assert validated.summary.risk_level == "판단 불가"
    assert validated.summary.risk_basis == []


def test_heading_only_items_are_removed_from_structured_answer() -> None:
    details = MaintenanceAnswerDetails(
        summary=MaintenanceSummary(
            status="안전관리자 확인 필요",
            risk_level="보통",
            risk_basis=[
                EvidenceBackedItem(content="18.", evidence_chunk_ids=["manual-1"]),
                EvidenceBackedItem(
                    content="설치 후 정상 동작 여부를 확인합니다.",
                    evidence_chunk_ids=["manual-1"],
                ),
            ],
            core_warning="작업 전 확인 필요",
        ),
        pre_checks=[
            EvidenceBackedItem(content="18.", evidence_chunk_ids=["manual-1"]),
        ],
        hazards=[
            MaintenanceHazard(
                name="목차 번호",
                content="18.",
                evidence_chunk_ids=["manual-1"],
            )
        ],
    )

    validated = validated_structured_answer(
        details,
        expected_type="maintenance_guide",
        sources=[_source("manual-1", "component_manual")],
    )

    assert validated is not None
    assert validated.summary.risk_basis == []
    assert validated.pre_checks == []
    assert validated.hazards == []


def test_checklist_items_require_retrieved_source() -> None:
    items = [
        ChatChecklistItem(
            content="검증된 항목",
            sequence=9,
            evidence_chunk_ids=["manual-1"],
        ),
        ChatChecklistItem(
            content="근거 없는 항목",
            sequence=10,
            evidence_chunk_ids=[],
        ),
    ]

    validated = validated_checklist_items(
        items,
        sources=[_source("manual-1", "component_manual")],
    )

    assert [item.content for item in validated] == ["검증된 항목"]
    assert validated[0].sequence == 1


def test_checklist_items_reject_source_label_meta_text() -> None:
    items = [
        ChatChecklistItem(
            content="light-curtain.pdf (116페이지) 근거를 원문에서 확인했다",
            sequence=1,
            evidence_chunk_ids=["manual-1"],
        ),
        ChatChecklistItem(
            content="작업 전 전원 차단·격리 및 재가동 방지 조치를 확인합니다.",
            sequence=2,
            evidence_chunk_ids=["manual-1"],
        ),
    ]

    validated = validated_checklist_items(
        items,
        sources=[_source("manual-1", "component_manual")],
    )

    assert [item.content for item in validated] == [
        "작업 전 전원 차단·격리 및 재가동 방지 조치를 확인합니다."
    ]


def test_conflict_requires_two_distinct_evidence_chunks() -> None:
    with pytest.raises(ValidationError):
        EvidenceConflict(
            content="근거 내용이 서로 다릅니다.",
            evidence_chunk_ids=["manual-1", "manual-1"],
        )
