from fastapi import APIRouter, HTTPException, Response, status

from app.schemas.speech import SpeechRequest
from app.services.speech import speech_service


router = APIRouter(prefix="/speech", tags=["speech"])


@router.post("/synthesize", response_class=Response)
async def synthesize_speech(payload: SpeechRequest) -> Response:
    try:
        audio = await speech_service.synthesize(payload.text, payload.speed)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supertonic 한국어 음성 생성에 실패했습니다. 모델 설치와 캐시 상태를 확인해 주세요.",
        ) from exc
    return Response(
        content=audio,
        media_type="audio/wav",
        headers={"Content-Disposition": 'inline; filename="safemaint-voice.wav"'},
    )
