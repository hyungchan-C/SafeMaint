import asyncio
from io import BytesIO
from threading import Lock

from app.core.config import settings


class FasterWhisperSttService:
    def __init__(self) -> None:
        self._model = None
        self._load_lock = Lock()

    def _get_model(self):
        if self._model is None:
            with self._load_lock:
                if self._model is None:
                    from faster_whisper import WhisperModel

                    self._model = WhisperModel(
                        settings.stt_model,
                        device=settings.stt_device,
                        compute_type=settings.stt_compute_type,
                    )
        return self._model

    def _transcribe_sync(self, audio: bytes) -> str:
        model = self._get_model()
        segments, _info = model.transcribe(
            BytesIO(audio),
            language=settings.stt_language,
            vad_filter=True,
        )
        return "".join(segment.text for segment in segments).strip()

    async def transcribe(self, audio: bytes) -> str:
        return await asyncio.to_thread(self._transcribe_sync, audio)


stt_service = FasterWhisperSttService()
