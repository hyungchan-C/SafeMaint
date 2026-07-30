from contextlib import asynccontextmanager
import logging
from uuid import uuid4

from fastapi import FastAPI
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import api_router
from app.api.routes.health import router as health_router
from app.core.config import settings
from app.services.speech import speech_service

# 별도 설정이 없으면 루트 로거가 기본값(WARNING)에 머물러서, chat.py의
# logger.info("chat_qwen ...") 같은 진단 로그가 핸들러까지 가지도 못하고 조용히
# 걸러진다. INFO로 올려서 docker compose logs backend에 실제로 찍히게 한다.
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # TTS 모델은 첫 요청이 들어올 때 지연 로드되도록 돼 있어서, 미리 예열해 두지
    # 않으면 재시작 후 첫 사용자가 모델 로딩 지연을 그대로 겪는다. 예열이 실패해도
    # (예: 모델 다운로드 불가) 서버 전체를 막지 않고, 이후 요청에서 다시 지연 로드를
    # 시도하도록 조용히 넘어간다.
    try:
        await speech_service.warmup()
    except Exception:
        logger.exception("TTS 모델 예열에 실패했습니다. 첫 요청에서 다시 시도합니다.")
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="SafeMaint AI 초기 위험성평가 및 안전관리 API",
    lifespan=lifespan,
)

# 개발 서버는 localhost뿐 아니라 같은 사설망의 브라우저에서도 접속한다.
# 운영 환경에서는 CORS_ORIGINS의 명시적인 목록만 사용한다.
development_origin_regex = (
    r"^https?://(?:localhost|127\.0\.0\.1|10(?:\.\d{1,3}){3}|"
    r"192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}):3000$"
    if settings.app_env == "development"
    else None
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_origin_regex=development_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def attach_request_id(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["x-request-id"] = request_id
    return response

app.include_router(health_router)
app.include_router(api_router, prefix=settings.api_prefix)
