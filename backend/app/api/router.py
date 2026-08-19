from fastapi import APIRouter

from app.api.routes import auth, knowledge_bases, users

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(knowledge_bases.router)
