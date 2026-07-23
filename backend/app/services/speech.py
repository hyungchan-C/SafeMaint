import asyncio
from collections.abc import AsyncIterator
from io import BytesIO
import re
from threading import Lock
import wave

from app.core.config import settings

_TTS_CHUNK_MAX_LENGTH = 120


KOREAN_TTS_REPLACEMENTS = {
    "LOTO": "잠금 표지 절차",
    "TBM": "티 비 엠",
    "PPE": "개인 보호구",
    "AI": "에이 아이",
    "CCTV": "씨 씨 티 브이",
}

# TTS는 쉼표에서 자연스럽게 쉬어가지만, 쉼표 없이 문장이 길어지면 급하게
# 읽는 경향이 있다. 쉼표가 하나도 없는 긴 문장에서 자연스러운 연결어미
# 뒤에 쉼표를 넣어 숨 쉴 지점을 만들어준다.
_BREATH_PARTICLES = (
    r"(?:하고|이고|고|하며|며|거나|지만|는데|은데|한데|"
    r"해서|여서|아서|어서|하여|한 뒤|한 후|거쳐|므로|니까|으니까)"
)
_MIN_LENGTH_FOR_BREATH = 22


def _add_breathing_room(sentence_match: re.Match[str]) -> str:
    sentence = sentence_match.group(0)
    if "," in sentence or len(sentence) < _MIN_LENGTH_FOR_BREATH:
        return sentence
    candidates = list(re.finditer(_BREATH_PARTICLES + r"(?=\s)", sentence))
    if not candidates:
        return sentence
    midpoint = len(sentence) / 2
    best = min(candidates, key=lambda m: abs(m.end() - midpoint))
    return sentence[: best.end()] + "," + sentence[best.end() :]


def prepare_korean_text(text: str) -> str:
    prepared = text.strip()
    # "검색된 유사 근거:" 목록은 화면에는 필요하지만 파일명을 그대로 읽어주면
    # 의미 없는 소리로 들리므로, 음성 안내에서는 이 구간 전체를 건너뛴다.
    prepared = re.sub(r"검색된 유사 근거:\n(?:-[^\n]*\n?)*", "", prepared)
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
    prepared = re.sub(r"[^.!?\n]+[.!?]", _add_breathing_room, prepared)
    # 줄 안의 공백은 다듬되 줄바꿈(문단 구분)은 살려둔다. supertonic의 chunk_text가
    # 빈 줄 단위로 문단을 나눠 각 문단 끝에 실제 묵음을 넣어주는데, 이전에는 모든
    # 줄바꿈을 공백 하나로 뭉개버려서 체크리스트/중지 기준 항목들이 쉼 없이 한
    # 문장처럼 이어 읽혔다. 각 줄을 별도 문단으로 만들어 항목마다 쉬어가게 한다.
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in prepared.splitlines()]
    prepared = "\n\n".join(line for line in lines if line)
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

                    self._tts = TTS(
                        auto_download=True,
                        intra_op_num_threads=settings.tts_num_threads,
                        inter_op_num_threads=settings.tts_num_threads,
                    )
                    self._voice_style = self._tts.get_voice_style(
                        voice_name=settings.tts_voice
                    )
        return self._tts, self._voice_style

    @staticmethod
    def _encode_wav(samples) -> bytes:
        import numpy as np

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

    def _synthesize_sync(self, text: str, speed: float) -> bytes:
        with self._generation_lock:
            tts, voice_style = self._get_tts()
            samples, _duration = tts.synthesize(
                text=prepare_korean_text(text),
                lang=settings.tts_language,
                voice_style=voice_style,
                total_steps=settings.tts_steps,
                speed=speed,
                max_chunk_length=_TTS_CHUNK_MAX_LENGTH,
                silence_duration=0.3,
                verbose=False,
            )
        return self._encode_wav(samples)

    def _synthesize_piece_sync(self, piece: str, speed: float) -> bytes:
        with self._generation_lock:
            tts, voice_style = self._get_tts()
            samples, _duration = tts.synthesize(
                text=piece,
                lang=settings.tts_language,
                voice_style=voice_style,
                total_steps=settings.tts_steps,
                speed=speed,
                # piece is already one pre-split chunk; keep it as a single unit.
                max_chunk_length=max(len(piece), _TTS_CHUNK_MAX_LENGTH),
                silence_duration=0.3,
                verbose=False,
            )
        return self._encode_wav(samples)

    async def synthesize(self, text: str, speed: float) -> bytes:
        return await asyncio.to_thread(self._synthesize_sync, text, speed)

    async def synthesize_stream(self, text: str, speed: float) -> AsyncIterator[bytes]:
        """Yield length-prefixed WAV frames (4-byte big-endian length + WAV bytes)
        as each text chunk finishes synthesizing, instead of waiting for the
        entire answer to be generated before sending anything back."""
        from supertonic.utils import chunk_text

        prepared = prepare_korean_text(text)
        pieces = chunk_text(prepared, _TTS_CHUNK_MAX_LENGTH)
        for piece in pieces:
            wav_bytes = await asyncio.to_thread(self._synthesize_piece_sync, piece, speed)
            yield len(wav_bytes).to_bytes(4, "big") + wav_bytes


speech_service = SupertonicSpeechService()
