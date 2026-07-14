from app.schemas.assessment import AssessmentRequest, RiskLevel
from app.services.risk_engine import RiskEngine


def test_unknown_work_requires_more_information() -> None:
    request = AssessmentRequest(
        site_name="A공장",
        equipment_name="설비 미정",
        task_type="기타 작업",
        description="아직 세부 작업 조건이 결정되지 않았습니다.",
    )

    hazards, checklist = RiskEngine().evaluate(request)

    assert hazards[0].accident_type == "미분류"
    assert hazards[0].risk_level is RiskLevel.LOW
    assert "제조사·모델·부품번호 확인" in checklist
