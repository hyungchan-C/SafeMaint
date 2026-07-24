from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_retrieval_access_scope
from app.db.models import User
from app.db.session import get_db
from app.schemas.assessment import (
    AssessmentRequest,
    AssessmentResponse,
    AssessmentSummaryResponse,
    ChatChecklistSaveRequest,
    ChecklistItemUpdateRequest,
    ChecklistItemUpdateResponse,
)
from app.schemas.chat import RetrievalAccessScope
from app.services.assessment import (
    AssessmentAccessDeniedError,
    AssessmentReferenceError,
    AssessmentService,
)


router = APIRouter(prefix="/assessments", tags=["assessments"])
service = AssessmentService()


def get_assessment_service() -> AssessmentService:
    return service


def _raise_assessment_error(error: Exception) -> NoReturn:
    if isinstance(error, AssessmentAccessDeniedError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error))
    if isinstance(error, AssessmentReferenceError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        )
    raise error


@router.post(
    "/preview",
    response_model=AssessmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_assessment_preview(
    payload: AssessmentRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    access_scope: Annotated[
        RetrievalAccessScope, Depends(get_retrieval_access_scope)
    ],
    db: Annotated[Session, Depends(get_db)],
    assessment_service: Annotated[
        AssessmentService, Depends(get_assessment_service)
    ],
) -> AssessmentResponse:
    """Create a non-authoritative draft within the authenticated site scope."""

    del current_user
    try:
        return assessment_service.create_preview(payload, db, access_scope)
    except (AssessmentAccessDeniedError, AssessmentReferenceError) as error:
        _raise_assessment_error(error)


@router.post(
    "",
    response_model=AssessmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_assessment(
    payload: AssessmentRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    access_scope: Annotated[
        RetrievalAccessScope, Depends(get_retrieval_access_scope)
    ],
    db: Annotated[Session, Depends(get_db)],
    assessment_service: Annotated[
        AssessmentService, Depends(get_assessment_service)
    ],
) -> AssessmentResponse:
    """Retrieve first, then persist the complete assessment in one transaction."""

    try:
        return assessment_service.create_and_save(
            payload,
            db,
            current_user,
            access_scope,
        )
    except (AssessmentAccessDeniedError, AssessmentReferenceError) as error:
        _raise_assessment_error(error)


@router.post(
    "/from-chat-checklist",
    response_model=AssessmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_assessment_from_chat_checklist(
    payload: ChatChecklistSaveRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    assessment_service: Annotated[
        AssessmentService, Depends(get_assessment_service)
    ],
) -> AssessmentResponse:
    """채팅 답변에 표시된 TBM 체크리스트를 그대로(규칙 엔진 재계산 없이) 저장한다."""

    return assessment_service.create_from_chat_checklist(payload, db, current_user)


@router.patch(
    "/{assessment_id}/checklist-items/{item_id}",
    response_model=ChecklistItemUpdateResponse,
)
def update_checklist_item(
    assessment_id: str,
    item_id: str,
    payload: ChecklistItemUpdateRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    access_scope: Annotated[
        RetrievalAccessScope, Depends(get_retrieval_access_scope)
    ],
    db: Annotated[Session, Depends(get_db)],
    assessment_service: Annotated[
        AssessmentService, Depends(get_assessment_service)
    ],
) -> ChecklistItemUpdateResponse:
    """Persist one TBM confirmation without changing assessment approval state."""

    response = assessment_service.update_checklist_item(
        assessment_id,
        item_id,
        payload.is_completed,
        db,
        current_user,
        access_scope,
    )
    if response is None:
        raise HTTPException(status_code=404, detail="Checklist item not found")
    return response


@router.get("", response_model=list[AssessmentSummaryResponse])
def list_assessments(
    current_user: Annotated[User, Depends(get_current_user)],
    access_scope: Annotated[
        RetrievalAccessScope, Depends(get_retrieval_access_scope)
    ],
    db: Annotated[Session, Depends(get_db)],
    assessment_service: Annotated[
        AssessmentService, Depends(get_assessment_service)
    ],
) -> list[AssessmentSummaryResponse]:
    """저장된 위험성평가 목록. all_sites 권한(admin/document_manager)이 있으면
    전체를, 없으면 본인 것과 배정된 사업장 것만 돌려준다."""

    return assessment_service.list_assessments(db, current_user, access_scope)


@router.get("/{assessment_id}", response_model=AssessmentResponse)
def get_assessment(
    assessment_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    access_scope: Annotated[
        RetrievalAccessScope, Depends(get_retrieval_access_scope)
    ],
    db: Annotated[Session, Depends(get_db)],
    assessment_service: Annotated[
        AssessmentService, Depends(get_assessment_service)
    ],
) -> AssessmentResponse:
    response = assessment_service.get_by_id(
        assessment_id,
        db,
        current_user,
        access_scope,
    )
    if response is None:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return response
