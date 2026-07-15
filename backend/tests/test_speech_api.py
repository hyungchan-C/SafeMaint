from fastapi.testclient import TestClient

from app.main import app
from app.services.speech import prepare_korean_text, speech_service


def test_speech_endpoint_returns_wav(monkeypatch) -> None:
    async def fake_synthesize(text: str, speed: float) -> bytes:
        assert text == "안전모를 착용하세요."
        assert speed == 1.0
        return b"RIFF-test-wave"

    monkeypatch.setattr(speech_service, "synthesize", fake_synthesize)
    response = TestClient(app).post(
        "/api/v1/speech/synthesize",
        json={"text": "안전모를 착용하세요.", "speed": 1.0},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content == b"RIFF-test-wave"


def test_speech_endpoint_rejects_oversized_text() -> None:
    response = TestClient(app).post(
        "/api/v1/speech/synthesize",
        json={"text": "가" * 2001},
    )
    assert response.status_code == 422


def test_korean_speech_preprocessing_expands_safety_terms() -> None:
    prepared = prepare_korean_text("CV-203의 LOTO 후 TBM을 진행합니다.")
    assert prepared == "C V 203의 잠금 표지 절차 후 티 비 엠을 진행합니다."
