from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.repositories.dashboard import DashboardRepository
from app.schemas.dashboard import DashboardSummary


router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary)
def get_dashboard_summary(
    db: Annotated[Session, Depends(get_db)],
) -> DashboardSummary:
    """Return metrics calculated from persisted assessments."""

    return DashboardRepository(db).get_summary()
