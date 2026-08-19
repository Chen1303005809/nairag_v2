from fastapi import APIRouter

from app.api.routes import auth, knowledge_bases, knowledge_content, search, users

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(knowledge_bases.router)
api_router.include_router(knowledge_content.router)
api_router.include_router(search.router)
