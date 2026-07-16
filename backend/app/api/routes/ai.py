from fastapi import APIRouter, HTTPException, status
from openai import OpenAIError

from app.core.config import settings
from app.schemas.ai import ChatRequest, ChatResponse
from app.services.ai import AIConfigurationError, AIService


router = APIRouter(prefix="/ai", tags=["ai"])


@router.post("/chat", response_model=ChatResponse)
def create_chat_response(payload: ChatRequest) -> ChatResponse:
    try:
        answer = AIService().answer(payload.question, payload.context)
    except AIConfigurationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    except OpenAIError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="OpenAI API 요청에 실패했습니다. API 키와 사용 한도를 확인해 주세요.",
        ) from error
    return ChatResponse(answer=answer, model=settings.openai_model)
