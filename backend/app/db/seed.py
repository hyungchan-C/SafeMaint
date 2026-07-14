from collections.abc import Sequence
from typing import TypedDict

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import ReferenceCode
from app.db.session import SessionLocal


class ReferenceCodeSeed(TypedDict):
    category: str
    code: str
    label: str
    sort_order: int


REFERENCE_CODES: Sequence[ReferenceCodeSeed] = (
    {"category": "assessment_status", "code": "draft", "label": "초안", "sort_order": 10},
    {"category": "assessment_status", "code": "pending_review", "label": "검토 대기", "sort_order": 20},
    {"category": "assessment_status", "code": "approved", "label": "승인", "sort_order": 30},
    {"category": "assessment_status", "code": "rejected", "label": "반려", "sort_order": 40},
    {"category": "risk_level", "code": "low", "label": "낮음", "sort_order": 10},
    {"category": "risk_level", "code": "medium", "label": "보통", "sort_order": 20},
    {"category": "risk_level", "code": "high", "label": "높음", "sort_order": 30},
    {"category": "accident_type", "code": "pinch", "label": "끼임", "sort_order": 10},
    {"category": "accident_type", "code": "electric_shock", "label": "감전", "sort_order": 20},
    {"category": "accident_type", "code": "fall", "label": "추락", "sort_order": 30},
    {"category": "accident_type", "code": "falling_object", "label": "낙하물", "sort_order": 40},
    {"category": "accident_type", "code": "collision", "label": "충돌", "sort_order": 50},
    {"category": "accident_type", "code": "fire_explosion", "label": "화재·폭발", "sort_order": 60},
    {"category": "task_type", "code": "inspection", "label": "점검", "sort_order": 10},
    {"category": "task_type", "code": "cleaning", "label": "청소", "sort_order": 20},
    {"category": "task_type", "code": "jam_removal", "label": "이물질 제거", "sort_order": 30},
    {"category": "task_type", "code": "replacement", "label": "부품 교체", "sort_order": 40},
    {"category": "task_type", "code": "repair", "label": "수리", "sort_order": 50},
    {"category": "energy_source", "code": "electric", "label": "전기", "sort_order": 10},
    {"category": "energy_source", "code": "hydraulic", "label": "유압", "sort_order": 20},
    {"category": "energy_source", "code": "pneumatic", "label": "공압", "sort_order": 30},
    {"category": "energy_source", "code": "mechanical", "label": "기계", "sort_order": 40},
    {"category": "energy_source", "code": "thermal", "label": "열", "sort_order": 50},
    {"category": "energy_source", "code": "chemical", "label": "화학", "sort_order": 60},
    {"category": "energy_source", "code": "gravity", "label": "중력", "sort_order": 70},
)


def seed_reference_data(session: Session) -> int:
    statement = insert(ReferenceCode).values(list(REFERENCE_CODES))
    statement = statement.on_conflict_do_update(
        constraint="uq_reference_codes_category_code",
        set_={
            "label": statement.excluded.label,
            "sort_order": statement.excluded.sort_order,
            "is_active": True,
            "updated_at": func.now(),
        },
    )
    try:
        result = session.execute(statement)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return max(result.rowcount or 0, 0)


def main() -> None:
    with SessionLocal() as session:
        affected_rows = seed_reference_data(session)
    print(f"SafeMaint reference seed completed: {affected_rows} rows applied")


if __name__ == "__main__":
    main()
