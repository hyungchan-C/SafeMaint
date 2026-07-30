from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_retrieval_access_scope
from app.db.session import get_db
from app.schemas.chat import ChatRequest, ChatResponse, RetrievalAccessScope
from app.services.chat import ChatService, get_chat_service


router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def create_chat_answer(
    payload: ChatRequest,
    access_scope: Annotated[
        RetrievalAccessScope, Depends(get_retrieval_access_scope)
    ],
    service: Annotated[ChatService, Depends(get_chat_service)],
    db: Annotated[Session, Depends(get_db)],
) -> ChatResponse:
    if payload.context.selected_document_ids and not access_scope.allow_company:
        if access_scope.requester_user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="선택한 PDF 문서로 답변하려면 다시 로그인해 주세요.",
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="선택한 PDF 문서를 읽을 권한이 없습니다.",
        )
    return await service.answer(payload, access_scope, db=db)
