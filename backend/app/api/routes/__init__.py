from fastapi import APIRouter

from app.api.routes import ai, assessments, auth, dashboard, speech


api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(assessments.router)
api_router.include_router(dashboard.router)
api_router.include_router(speech.router)
api_router.include_router(ai.router)
