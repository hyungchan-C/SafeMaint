from typing import Annotated

from fastapi import APIRouter, Depends

from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat import ChatService, get_chat_service


router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def create_chat_answer(
    payload: ChatRequest,
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> ChatResponse:
    # The teammate-owned authentication dependency will supply a scoped
    # RetrievalAccessScope here. Until then the service defaults to public-only.
    return await service.answer(payload)
