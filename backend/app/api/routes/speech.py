from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status

from app.schemas.speech import SpeechRequest, TranscribeResponse
from app.services.speech import speech_service
from app.services.stt import stt_service


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


@router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe_speech(file: Annotated[UploadFile, File()]) -> TranscribeResponse:
    audio = await file.read()
    if not audio:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="빈 오디오 파일은 변환할 수 없습니다.",
        )
    try:
        text = await stt_service.transcribe(audio)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="음성 인식에 실패했습니다. 모델 설치와 캐시 상태를 확인해 주세요.",
        ) from exc
    return TranscribeResponse(text=text)
