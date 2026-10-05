"""HTTP route handlers. `api_router` is mounted under ``settings.api_prefix`` (/api/v1)."""

from fastapi import APIRouter, Depends

from app.api import (
    auth,
    concepts,
    documents,
    goals,
    health,
    mastery,
    profile,
    sessions,
    study_plan,
    subjects,
)
from app.api.deps import rate_limit_api

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)

# Authenticated, general-API-rate-limited routes.
_protected = APIRouter(dependencies=[Depends(rate_limit_api)])
_protected.include_router(profile.router)
_protected.include_router(subjects.router)
_protected.include_router(documents.router)
_protected.include_router(concepts.router)
_protected.include_router(concepts.search_router)
_protected.include_router(sessions.router)
_protected.include_router(goals.router)
_protected.include_router(study_plan.router)
_protected.include_router(mastery.router)
api_router.include_router(_protected)
# The session WebSocket authenticates with a single-use ticket, not a bearer token.
api_router.include_router(sessions.ws_router)

__all__ = ["api_router"]
