from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.db.session import create_session_factory
from app.services.attachment_storage import create_attachment_storage
from app.services.embedding import create_reranker_provider
from app.services.index_backend import MilvusHttpWriter
from app.services.llm import create_llm_provider
from app.services.ocr import create_ocr_provider
from app.services.retrieval import create_search_index_backend
from app.services.supplemental_retrieval import create_supplemental_retriever
from app.services.users import bootstrap_initial_admin


def create_app(
    *,
    settings: Settings | None = None,
    db_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> FastAPI:
    active_settings = settings or get_settings()
    active_session_factory = db_session_factory or create_session_factory(
        active_settings.database_url_with_password
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await app.state.attachment_storage.initialize()
        await bootstrap_initial_admin(app.state.session_factory, app.state.settings)
        # This only starts a background monitor.  It does not wait for, or
        # make a request to, the independently deployed LightRAG service.
        await app.state.supplemental_retriever.start()
        try:
            yield
        finally:
            await app.state.supplemental_retriever.aclose()

    app = FastAPI(title=active_settings.app_name, version="0.1.0", lifespan=lifespan)
    app.state.settings = active_settings
    app.state.session_factory = active_session_factory
    app.state.search_index_backend = create_search_index_backend(active_settings)
    app.state.reranker_provider = create_reranker_provider(active_settings)
    app.state.milvus_collection_manager = (
        MilvusHttpWriter(
            active_settings.milvus_url,
            token=active_settings.milvus_token,
            timeout_seconds=active_settings.embedding_timeout_seconds,
        )
        if active_settings.index_backend_mode == "milvus"
        and active_settings.milvus_url is not None
        else None
    )
    app.state.ocr_provider = create_ocr_provider(active_settings)
    app.state.llm_provider = create_llm_provider(active_settings)
    app.state.attachment_storage = create_attachment_storage(active_settings)
    app.state.supplemental_retriever = create_supplemental_retriever(active_settings)

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(api_router)
    return app


app = create_app()
