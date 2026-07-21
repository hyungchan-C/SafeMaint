from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SafetyGuidance:
    subject: str
    hazards: tuple[str, ...]
    actions: tuple[str, ...]
    manual_note: str


CONVEYOR_ACTIONS = (
    "운전 정지 버튼만 누르지 말고 주 전원을 차단한 뒤 개인 잠금장치와 작업표지를 적용합니다(LOTO).",
    "현장 조작반과 중앙제어실에 작업 사실을 알리고, 재기동 금지 상태를 직접 확인합니다.",
    "벨트, 풀리, 롤러가 완전히 정지했는지 확인하고 장력, 중력, 공압 등 잔류에너지를 제거하거나 고정합니다.",
    "방호덮개를 제거하기 전 작업구역을 통제하고, 작업 후 덮개와 비상정지장치를 원상복구합니다.",
    "공구와 인원이 위험구역에서 빠진 것을 확인한 뒤 안전관리자 입회하에 시험 운전합니다.",
)

TBM_STOP_RULES = (
    "전원 차단과 개인 잠금장치 적용 여부가 확인되지 않을 때",
    "벨트, 롤러, 풀리의 잔류 장력 또는 낙하 가능 부품을 고정하지 못했을 때",
    "방호장치, 비상정지장치, 인터록 복구 상태를 확인하지 못했을 때",
    "제조사 매뉴얼의 부품 규격, 장력 조정값, 정렬 기준, 체결 토크를 확인하지 못했을 때",
    "작업자 간 신호, 감시자, 재기동 금지 절차가 합의되지 않았을 때",
)


def build_safety_guidance(question: str) -> SafetyGuidance:
    normalized = " ".join(question.lower().split())
    is_conveyor = any(
        keyword in normalized
        for keyword in ("컨베이어", "콘베이어", "conveyor", "벨트", "belt", "풀리", "롤러", "roller")
    )
    is_bearing = any(keyword in normalized for keyword in ("베어링", "축받이", "bearing"))
    is_replacement = any(keyword in normalized for keyword in ("교체", "정비", "수리", "replacement", "replace"))
    is_cleaning = any(keyword in normalized for keyword in ("청소", "이물질", "제거", "clean", "remove"))

    if is_conveyor and is_bearing:
        return SafetyGuidance(
            subject="컨베이어 베어링 교체 작업",
            hazards=("예기치 않은 기동", "벨트·풀리 끼임", "잔류 장력", "부품 낙하·협착"),
            actions=CONVEYOR_ACTIONS
            + (
                "베어링과 축의 중량을 확인하고 인양구 또는 받침대를 사용해 분리 중 낙하와 축 처짐을 방지합니다.",
                "베어링 형식, 체결 토크, 윤활유 종류와 정렬 허용치는 해당 제조사 매뉴얼 기준으로 확인합니다.",
            ),
            manual_note=(
                "현재 공용 안전자료만으로는 베어링 상세 탈거 순서, 체결 토크, 윤활 조건을 확정할 수 없습니다."
            ),
        )

    if is_conveyor and is_cleaning:
        return SafetyGuidance(
            subject="컨베이어 이물질 제거·청소 작업",
            hazards=("회전체 끼임", "예기치 않은 기동", "미끄러짐", "세척제·분진 노출"),
            actions=CONVEYOR_ACTIONS
            + (
                "운전 중 손, 걸레, 빗자루 또는 에어건을 벨트와 롤러 사이에 넣지 않습니다.",
                "청소 공구와 세척제는 설비 사양 및 물질안전보건자료(SDS)에 맞는 것을 사용하고 바닥 오염은 즉시 제거합니다.",
            ),
            manual_note=(
                "세척제, 물 사용 가능 여부와 청소 주기는 제조사 매뉴얼 및 현장 작업표준을 우선 적용해야 합니다."
            ),
        )

    if is_conveyor and is_replacement:
        return SafetyGuidance(
            subject="컨베이어 부품 교체 작업",
            hazards=("예기치 않은 기동", "벨트·풀리 끼임", "잔류 장력", "부품 낙하·협착"),
            actions=CONVEYOR_ACTIONS
            + (
                "교체 대상 부품의 품번, 호환성, 중량, 인양 필요 여부를 작업 전에 확인합니다.",
                "분해·조립 순서, 장력 해제 방법, 장력 조정값, 정렬 기준과 체결 토크는 제조사 매뉴얼로 확인합니다.",
            ),
            manual_note=(
                "현재 공용 안전자료만으로는 실제 교체 순서와 수치 조건을 확정할 수 없으므로 제조사 PDF/매뉴얼이 필요합니다."
            ),
        )

    if is_conveyor:
        return SafetyGuidance(
            subject="컨베이어 정비 작업",
            hazards=("회전체 끼임", "예기치 않은 기동", "잔류에너지"),
            actions=CONVEYOR_ACTIONS,
            manual_note=(
                "부품별 분해·조립 기준은 해당 설비와 부품의 제조사 매뉴얼을 확인해야 합니다."
            ),
        )

    return SafetyGuidance(
        subject="설비 정비 작업",
        hazards=("예기치 않은 기동", "잔류에너지", "협착·낙하"),
        actions=(
            "작업 대상, 작업 범위와 에너지원 종류를 먼저 확인합니다.",
            "전기, 공압, 유압 등 모든 에너지원을 차단하고 개인 잠금장치와 작업표지를 적용합니다(LOTO).",
            "잔류에너지를 제거한 뒤 무전압, 무압력, 정지 상태를 측정 또는 시험으로 확인합니다.",
            "위험구역을 통제하고 적합한 보호구와 공구를 사용합니다.",
            "작업 완료 후 안전관리자 확인을 거쳐 방호장치 복구와 시험 운전을 진행합니다.",
        ),
        manual_note=(
            "정확한 분해·조립 방법과 설정값은 해당 설비의 제조사 매뉴얼을 확인해야 합니다."
        ),
    )


def format_safety_answer(question: str, source_titles: list[str] | None = None) -> str:
    guidance = build_safety_guidance(question)
    hazards = ", ".join(guidance.hazards)
    checklist = "\n".join(f"[ ] {action}" for action in guidance.actions)
    stop_rules = "\n".join(f"- {rule}" for rule in TBM_STOP_RULES)
    source_note = ""
    if source_titles:
        unique_titles = list(dict.fromkeys(source_titles))[:3]
        source_note = "\n\n검색된 유사 근거:\n" + "\n".join(
            f"- {title}" for title in unique_titles
        )

    return (
        f"{guidance.subject}에는 {hazards} 위험이 있습니다.\n\n"
        "현재 근거로 가능한 범위:\n"
        "- 공용 안전자료와 유사 사고사례 기준으로 위험요인, 작업 전 확인사항, 중지 기준은 안내할 수 있습니다.\n"
        f"- {guidance.manual_note}\n\n"
        "TBM 체크리스트:\n"
        f"{checklist}\n\n"
        "작업 중지 기준:\n"
        f"{stop_rules}"
        f"{source_note}\n\n"
        "이 안내는 작업 승인이 아닙니다. 현장 상태와 제조사 매뉴얼을 확인하고 "
        "안전관리자의 최종 확인 전에는 작업을 시작하지 마세요."
    )
