from fastapi import APIRouter
from app.api.v1.platforms import router as platforms_router
from app.api.v1.telemetry import router as telemetry_router
from app.api.v1.audience import router as audience_router
from app.api.v1.content import router as content_router
from app.api.v1.schedule import router as schedule_router
from app.api.v1.experiments import router as experiments_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.logs import router as logs_router
from app.api.v1.demand import router as demand_router

api_router = APIRouter()

api_router.include_router(platforms_router)
api_router.include_router(telemetry_router)
api_router.include_router(audience_router)
api_router.include_router(content_router)
api_router.include_router(schedule_router)
api_router.include_router(experiments_router)
api_router.include_router(dashboard_router)
api_router.include_router(logs_router)
api_router.include_router(demand_router)
