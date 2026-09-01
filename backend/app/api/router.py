from fastapi import APIRouter

from app.api.routes import (
    attachment_ingestion,
    auth,
    drafts,
    intelligent_ingestion,
    knowledge_bases,
    knowledge_content,
    search,
    supplemental,
    users,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(knowledge_bases.router)
api_router.include_router(knowledge_content.router)
api_router.include_router(attachment_ingestion.router)
api_router.include_router(drafts.router)
api_router.include_router(intelligent_ingestion.router)
api_router.include_router(search.router)
api_router.include_router(supplemental.router)
