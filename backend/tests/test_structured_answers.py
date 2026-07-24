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
    enriched_structured_answer,
    source_based_checklist_items,
    source_based_fallback,
    validated_checklist_items,
    validated_structured_answer,
)


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


def test_public_maintenance_fallback_exposes_reference_without_inventing_tbm() -> None:
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
    assert details.hazards == []
    assert details.manual_steps == []
    assert details.related_regulations_and_incidents
    assert details.summary.risk_level == "판단 불가"
    assert details.summary.risk_basis == []
    assert checklist == []
    assert "[자료유형]" not in details.related_regulations_and_incidents[0].content


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
    assert details.hazards == []
    assert details.stop_conditions == []
    assert details.related_regulations_and_incidents
    assert checklist == []


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
    assert details.pre_checks == []
    assert details.hazards == []
    assert details.manual_steps == []
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
    assert details.main_contents
    assert details.main_contents[0].evidence_chunk_ids == ["light-1"]
    assert details.related_equipment == []
    assert details.related_components == []
    assert details.supported_tasks == []


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
    assert details.main_roles
    assert details.main_roles[0].evidence_chunk_ids == ["light-1"]
    assert details.usage_locations == []
    assert details.precautions == []


def test_empty_qwen_component_sections_are_not_rule_backfilled() -> None:
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
    assert enriched.main_roles == []
    assert enriched.usage_locations == []
    assert enriched.precautions == []


def test_empty_qwen_document_sections_are_not_rule_backfilled() -> None:
    fallback = source_based_fallback(
        "document_qa",
        [
            ChatSource(
                document_id="doc-light",
                chunk_id="light-1",
                title="라이트 커튼 사용자 매뉴얼",
                source_type="equipment_manual",
                section="모델 구성",
                excerpt="라이트 커튼은 세이프티 컴포넌트이며 설치, 점검, PC 설정 항목을 포함한다.",
                similarity=0.8,
            )
        ],
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
    assert enriched.related_components == []
    assert enriched.supported_tasks == []


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


def test_manual_steps_preserve_qwen_text_without_rule_rewriting() -> None:
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
    assert [step.content for step in validated.manual_steps] == [
        (
            "경고: 본 제품은 안전 관련 장치이며 설치, 배선, 점검, 유지보수, "
            "주변 장치와의 연결 상태, 감지 영역, 안전거리, 현장 조건 등 여러 "
            "조건을 모두 고려해야 합니다. 자세한 내용은 각 장의 설명과 표를 "
            "참조하십시오. 임의 변경은 위험할 수 있습니다."
        ),
        "설치 전 광축 정렬 상태를 확인합니다.",
    ]


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


def test_component_items_preserve_qwen_text_without_rule_rewriting() -> None:
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
        long_raw,
        "기능 변경 후 정상 동작 여부를 확인합니다.",
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
    assert [item.content for item in validated.summary.risk_basis] == [
        "설치 후 정상 동작 여부를 확인합니다."
    ]
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
