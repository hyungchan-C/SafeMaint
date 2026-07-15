from datetime import datetime, time, timezone

from sqlalchemy import case, distinct, func, select
from sqlalchemy.orm import Session

from app.db.models import Assessment, AssessmentHazard, ChecklistItem
from app.schemas.dashboard import DashboardSummary


class DashboardRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_summary(self) -> DashboardSummary:
        today_start = datetime.combine(
            datetime.now(timezone.utc).date(), time.min, tzinfo=timezone.utc
        )

        today_tasks = self.session.scalar(
            select(func.count(Assessment.id)).where(Assessment.created_at >= today_start)
        ) or 0

        high_risk_tasks = self.session.scalar(
            select(func.count(distinct(Assessment.id)))
            .join(AssessmentHazard)
            .where(
                Assessment.created_at >= today_start,
                AssessmentHazard.risk_level == "high",
            )
        ) or 0

        pending_reviews = self.session.scalar(
            select(func.count(Assessment.id)).where(
                Assessment.status == "pending_review"
            )
        ) or 0

        checklist_counts = self.session.execute(
            select(
                func.count(ChecklistItem.id),
                func.sum(case((ChecklistItem.is_completed.is_(True), 1), else_=0)),
            )
            .join(Assessment)
            .where(Assessment.created_at >= today_start)
        ).one()
        total_items = int(checklist_counts[0] or 0)
        completed_items = int(checklist_counts[1] or 0)
        completion_rate = (
            round(completed_items / total_items * 100) if total_items else 0
        )

        return DashboardSummary(
            today_tasks=int(today_tasks),
            high_risk_tasks=int(high_risk_tasks),
            checklist_completion_rate=completion_rate,
            pending_reviews=int(pending_reviews),
        )
