from fastapi import APIRouter
from backend.api.v1.endpoints import deals

api_router = APIRouter()
api_router.include_router(deals.router, prefix="/deals", tags=["Deals"])
