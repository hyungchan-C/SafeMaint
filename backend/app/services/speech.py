import asyncio
from io import BytesIO
import re
from threading import Lock
import wave

from app.core.config import settings


KOREAN_TTS_REPLACEMENTS = {
    "LOTO": "잠금 표지 절차",
    "TBM": "티 비 엠",
    "PPE": "개인 보호구",
    "AI": "에이 아이",
    "CCTV": "씨 씨 티 브이",
}


def prepare_korean_text(text: str) -> str:
    prepared = text.strip()
    for abbreviation, spoken in KOREAN_TTS_REPLACEMENTS.items():
        prepared = re.sub(
            rf"(?<![A-Za-z]){abbreviation}(?![A-Za-z])",
            spoken,
            prepared,
            flags=re.IGNORECASE,
        )
    prepared = re.sub(
        r"(?<![A-Za-z0-9])([A-Za-z]+)-([0-9]+)(?![A-Za-z0-9])",
        lambda match: f"{' '.join(match.group(1).upper())} {match.group(2)}",
        prepared,
    )
    prepared = re.sub(r"[•·▶▷■□]", ", ", prepared)
    prepared = re.sub(r"\s+", " ", prepared)
    return prepared


class SupertonicSpeechService:
    def __init__(self) -> None:
        self._tts = None
        self._voice_style = None
        self._pipeline_lock = Lock()
        self._generation_lock = Lock()

    def _get_tts(self):
        if self._tts is None:
            with self._pipeline_lock:
                if self._tts is None:
                    from supertonic import TTS

                    self._tts = TTS(auto_download=True)
                    self._voice_style = self._tts.get_voice_style(
                        voice_name=settings.tts_voice
                    )
        return self._tts, self._voice_style

    def _synthesize_sync(self, text: str, speed: float) -> bytes:
        import numpy as np
        with self._generation_lock:
            tts, voice_style = self._get_tts()
            samples, _duration = tts.synthesize(
                text=prepare_korean_text(text),
                lang=settings.tts_language,
                voice_style=voice_style,
                total_steps=settings.tts_steps,
                speed=speed,
                max_chunk_length=120,
                silence_duration=0.3,
                verbose=False,
            )

        samples = np.asarray(samples, dtype=np.float32).reshape(-1)
        samples = np.clip(samples, -1.0, 1.0)
        pcm = (samples * 32767).astype("<i2").tobytes()
        output = BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(44100)
            wav.writeframes(pcm)
        return output.getvalue()

    async def synthesize(self, text: str, speed: float) -> bytes:
        return await asyncio.to_thread(self._synthesize_sync, text, speed)


speech_service = SupertonicSpeechService()
