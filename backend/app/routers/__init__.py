from fastapi import APIRouter

from app.routers import core, restitch

api = APIRouter()
api.include_router(core.router)
api.include_router(restitch.router)
