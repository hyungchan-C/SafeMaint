from pydantic import BaseModel, Field


class DashboardSummary(BaseModel):
    today_tasks: int = Field(ge=0)
    high_risk_tasks: int = Field(ge=0)
    checklist_completion_rate: int = Field(ge=0, le=100)
    pending_reviews: int = Field(ge=0)
