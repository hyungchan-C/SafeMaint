from fastapi import APIRouter

from app.api.routes import assessments, auth, dashboard


api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(assessments.router)
api_router.include_router(auth.router)
api_router.include_router(dashboard.router)
