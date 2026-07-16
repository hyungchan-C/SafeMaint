from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import api_router
from app.api.routes.health import router as health_router
from app.core.config import settings


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="SafeMaint AI 초기 위험성평가 및 안전관리 API",
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

app.include_router(health_router)
app.include_router(api_router, prefix=settings.api_prefix)
