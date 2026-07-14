from fastapi import APIRouter, status

from app.schemas.assessment import AssessmentRequest, AssessmentResponse
from app.services.assessment import AssessmentService


router = APIRouter(prefix="/assessments", tags=["assessments"])
service = AssessmentService()


@router.post(
    "/preview",
    response_model=AssessmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_assessment_preview(payload: AssessmentRequest) -> AssessmentResponse:
    """Create a non-authoritative draft before RAG is connected."""

    return await service.create_preview(payload)
