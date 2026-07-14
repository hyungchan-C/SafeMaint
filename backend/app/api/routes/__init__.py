from fastapi import APIRouter

from app.api.routes import assessments, dashboard


api_router = APIRouter()
api_router.include_router(assessments.router)
api_router.include_router(dashboard.router)
