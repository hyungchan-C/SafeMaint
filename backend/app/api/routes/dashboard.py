from fastapi import APIRouter

from app.schemas.dashboard import DashboardSummary


router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary)
def get_dashboard_summary() -> DashboardSummary:
    """Return empty initial metrics until persistence is connected."""

    return DashboardSummary(
        today_tasks=0,
        high_risk_tasks=0,
        checklist_completion_rate=0,
        pending_reviews=0,
    )
