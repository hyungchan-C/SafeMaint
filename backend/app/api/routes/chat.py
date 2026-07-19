from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import get_retrieval_access_scope
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
) -> ChatResponse:
    return await service.answer(payload, access_scope)
