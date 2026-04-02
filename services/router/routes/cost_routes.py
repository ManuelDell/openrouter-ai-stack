"""
cost_routes.py — FastAPI endpoints for cost dashboard.

Single responsibility: HTTP layer for cost data queries.
All data access goes through cost_tracker, never direct DB calls here.
"""

from fastapi import APIRouter, Query
from utils.cost_tracker import (
    query_by_feature,
    query_by_model,
    query_history,
    query_stats,
    query_today,
)

router = APIRouter(prefix="/api/costs", tags=["costs"])


@router.get("/today")
async def costs_today():
    return query_today()


@router.get("/stats")
async def costs_stats():
    return query_stats()


@router.get("/history")
async def costs_history(days: int = Query(default=7, ge=1, le=90)):
    return {"entries": query_history(days), "days": days}


@router.get("/by_feature")
async def costs_by_feature():
    return query_by_feature()


@router.get("/by_model")
async def costs_by_model():
    return query_by_model()
