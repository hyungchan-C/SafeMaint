from dataclasses import dataclass
from collections.abc import Iterable

from app.schemas.assessment import (
    AssessmentRequest,
    EvidenceItem,
    HazardItem,
    RiskLevel,
)


@dataclass(frozen=True, slots=True)
class RiskRule:
    keywords: tuple[str, ...]
    name: str
    accident_type: str
    likelihood: int
    severity: int
    safety_actions: tuple[str, ...]
    checklist: tuple[str, ...]


RULES: tuple[RiskRule, ...] = (
    RiskRule(
        keywords=("컨베이어", "벨트", "체인", "베어링", "회전체"),
        name="회전체 및 구동부 끼임 위험",
        accident_type="끼임",
        likelihood=3,
        severity=4,
        safety_actions=(
            "설비의 모든 에너지원을 차단하고 잠금·표지 절차를 확인합니다.",
            "회전체가 완전히 정지했는지 직접 확인합니다.",
            "방호덮개를 임의로 해제한 상태에서 작업하지 않습니다.",
        ),
        checklist=(
            "주전원 차단과 잠금·표지 상태 확인",
            "회전체 완전 정지 확인",
            "방호덮개와 작업구역 출입통제 확인",
        ),
    ),
    RiskRule(
        keywords=("정비", "점검", "청소", "교체", "이물질", "조정"),
        name="설비 불시 재가동 위험",
        accident_type="불시기동",
        likelihood=3,
        severity=4,
        safety_actions=(
            "정비 시작 전 작업허가와 설비 정지 상태를 확인합니다.",
            "에너지 차단 지점을 식별하고 잔류 에너지를 제거합니다.",
            "재가동 권한자와 작업자 사이의 연락 절차를 확인합니다.",
        ),
        checklist=(
            "작업허가 및 담당자 확인",
            "잔류 에너지 제거 확인",
            "재가동 금지 표지와 연락 절차 확인",
        ),
    ),
    RiskRule(
        keywords=("전기", "모터", "판넬", "전압", "전원", "감전"),
        name="전기에너지 접촉 위험",
        accident_type="감전",
        likelihood=2,
        severity=4,
        safety_actions=(
            "차단기 개방 후 무전압 상태를 적절한 계측기로 확인합니다.",
            "잔류전하와 유도전압 가능성을 확인합니다.",
            "사업장 전기작업 절차와 적합한 보호구를 확인합니다.",
        ),
        checklist=(
            "무전압 상태 확인",
            "잔류전하 방전 여부 확인",
            "전기작업 보호구와 절연공구 확인",
        ),
    ),
    RiskRule(
        keywords=("용접", "불꽃", "화재", "가연성", "고온"),
        name="고온·불꽃에 의한 화재 위험",
        accident_type="화재",
        likelihood=2,
        severity=4,
        safety_actions=(
            "가연물을 제거하고 화기작업 허가 여부를 확인합니다.",
            "적합한 소화설비와 화재감시자를 배치합니다.",
            "작업 종료 후 잔불과 고온부를 확인합니다.",
        ),
        checklist=(
            "화기작업 허가 확인",
            "가연물 제거와 소화설비 배치 확인",
            "작업 종료 후 잔불 점검 계획 확인",
        ),
    ),
)


class RiskEngine:
    def evaluate(
        self,
        request: AssessmentRequest,
        evidence: Iterable[EvidenceItem] = (),
    ) -> tuple[list[HazardItem], list[str]]:
        searchable_text = " ".join(
            filter(
                None,
                (
                    request.equipment_name,
                    request.manufacturer,
                    request.model_number,
                    request.component_name,
                    request.task_type,
                    " ".join(request.energy_sources),
                    request.description,
                ),
            )
        ).lower()

        matched_rules = [
            rule
            for rule in RULES
            if any(keyword in searchable_text for keyword in rule.keywords)
        ]

        if not matched_rules:
            matched_rules = [
                RiskRule(
                    keywords=(),
                    name="작업 조건 추가 확인 필요",
                    accident_type="미분류",
                    likelihood=2,
                    severity=2,
                    safety_actions=(
                        "설비·부품·에너지원과 작업 절차를 추가로 확인합니다.",
                        "근거 문서가 연결되기 전에는 작업 가능 여부를 확정하지 않습니다.",
                    ),
                    checklist=(
                        "제조사·모델·부품번호 확인",
                        "사업장 작업표준서와 담당자 확인",
                    ),
                )
            ]

        hazards = [self._to_hazard(rule) for rule in matched_rules]
        checklist = self._deduplicate(
            item for rule in matched_rules for item in rule.checklist
        )
        selected_evidence = list(evidence)[:3]
        if selected_evidence:
            evidence_actions = [
                (
                    f"근거 [{item.retrieval_rank}] {item.title}"
                    f"{f' ({item.page_start}쪽)' if item.page_start else ''}의 "
                    "관련 절차를 원문과 대조합니다."
                )
                for item in selected_evidence
            ]
            hazards = [
                hazard.model_copy(
                    update={
                        "safety_actions": self._deduplicate(
                            [*hazard.safety_actions, *evidence_actions]
                        )
                    }
                )
                for hazard in hazards
            ]
            checklist = self._deduplicate([*checklist, *evidence_actions])
        else:
            checklist = self._deduplicate(
                [
                    *checklist,
                    "공통 안전수칙(문서 근거 없음): 제조사 매뉴얼과 현장 절차를 추가 확인",
                ]
            )
        return hazards, checklist

    @staticmethod
    def _to_hazard(rule: RiskRule) -> HazardItem:
        score = rule.likelihood * rule.severity
        if score >= 12:
            risk_level = RiskLevel.HIGH
        elif score >= 6:
            risk_level = RiskLevel.MEDIUM
        else:
            risk_level = RiskLevel.LOW

        return HazardItem(
            name=rule.name,
            accident_type=rule.accident_type,
            likelihood=rule.likelihood,
            severity=rule.severity,
            score=score,
            risk_level=risk_level,
            safety_actions=list(rule.safety_actions),
        )

    @staticmethod
    def _deduplicate(items: Iterable[str]) -> list[str]:
        return list(dict.fromkeys(items))
