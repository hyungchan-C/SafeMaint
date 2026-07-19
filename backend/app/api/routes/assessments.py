from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.assessment import AssessmentRequest, AssessmentResponse
from app.services.assessment import AssessmentService


router = APIRouter(prefix="/assessments", tags=["assessments"])
service = AssessmentService()


@router.post(
    "/preview",
    response_model=AssessmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_assessment_preview(payload: AssessmentRequest) -> AssessmentResponse:
    """Create a non-authoritative draft with public RAG evidence when available."""

    return service.create_preview(payload)


@router.post(
    "",
    response_model=AssessmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_assessment(
    payload: AssessmentRequest,
    db: Annotated[Session, Depends(get_db)],
) -> AssessmentResponse:
    """Retrieve first, then persist the complete assessment in one transaction."""

    return service.create_and_save(payload, db)


@router.get("/{assessment_id}", response_model=AssessmentResponse)
def get_assessment(
    assessment_id: str,
    db: Annotated[Session, Depends(get_db)],
) -> AssessmentResponse:
    response = service.get_by_id(assessment_id, db)
    if response is None:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return response
