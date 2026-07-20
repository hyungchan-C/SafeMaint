from fastapi import APIRouter

from app.api.routes import (
    assessments,
    auth,
    chat,
    dashboard,
    documents,
    gps,
    speech,
    vision,
)


api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(chat.router)
api_router.include_router(assessments.router)
api_router.include_router(dashboard.router)
api_router.include_router(speech.router)
api_router.include_router(vision.router)
api_router.include_router(documents.router)
api_router.include_router(gps.router)
