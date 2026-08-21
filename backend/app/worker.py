from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
from collections.abc import Awaitable, Callable
from contextlib import suppress

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.db.session import create_session_factory
from app.services.embedding import QwenEmbeddingProvider
from app.services.index_backend import (
    LocalArtifactIndexBackend,
    MilvusHttpWriter,
    MilvusIndexBackend,
)
from app.services.index_jobs import IndexBackend, IndexWorkerResult, run_index_worker_once
from app.services.intelligent_ingestion import run_ingestion_worker_once
from app.services.llm import LlmProvider, create_llm_provider

logger = logging.getLogger("nairag.index-worker")


def create_index_backend(settings: Settings) -> IndexBackend:
    if settings.index_backend_mode == "local_artifact":
        return LocalArtifactIndexBackend(settings.index_artifact_dir)
    if settings.embedding_service_url is None or settings.milvus_url is None:
        raise RuntimeError("Milvus indexing requires embedding and Milvus service URLs")
    embedding_provider = QwenEmbeddingProvider(
        settings.embedding_service_url,
        api_key=settings.embedding_api_key,
        model_name=settings.embedding_model,
        dimension=settings.embedding_dimension,
        timeout_seconds=settings.embedding_timeout_seconds,
    )
    return MilvusIndexBackend(
        writer=MilvusHttpWriter(
            settings.milvus_url,
            token=settings.milvus_token,
            timeout_seconds=settings.embedding_timeout_seconds,
        ),
        embedding_provider=embedding_provider,
    )


def resolve_worker_id(settings: Settings) -> str:
    """Return a bounded, stable-per-process lease owner identifier."""

    configured = (settings.worker_id or "").strip()
    if configured:
        return configured[:120]
    hostname = os.environ.get("HOSTNAME", "local-worker").strip() or "local-worker"
    return f"{hostname}:{os.getpid()}"[:120]


async def wait_for_stop(stop_event: asyncio.Event, timeout: float) -> bool:
    """Wait for shutdown, returning whether the event was set."""

    try:
        await asyncio.wait_for(stop_event.wait(), timeout=timeout)
    except TimeoutError:
        return False
    return True


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(signal_name, stop_event.set)


async def run_worker(
    *,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    backend: IndexBackend | None = None,
    llm_provider: LlmProvider | None = None,
    stop_event: asyncio.Event | None = None,
    on_result: Callable[[IndexWorkerResult], Awaitable[None] | None] | None = None,
) -> None:
    """Run the durable index worker until SIGTERM or ``stop_event``.

    The worker owns no publication state. Each iteration claims one leased job,
    writes the derived artifact through the backend, and lets the index-job service
    commit either publication or retry state in PostgreSQL.
    """

    active_settings = settings or get_settings()
    owns_factory = session_factory is None
    active_factory = session_factory or create_session_factory(
        active_settings.database_url_with_password
    )
    active_backend = backend or create_index_backend(active_settings)
    active_llm_provider = llm_provider or create_llm_provider(active_settings)
    active_stop_event = stop_event or asyncio.Event()
    if stop_event is None:
        _install_signal_handlers(active_stop_event)

    worker_id = resolve_worker_id(active_settings)
    logger.info(
        "worker started worker_id=%s artifact_dir=%s llm_configured=%s",
        worker_id,
        active_settings.index_artifact_dir,
        active_llm_provider is not None,
    )
    try:
        while not active_stop_event.is_set():
            try:
                result = await run_index_worker_once(
                    active_factory,
                    worker_id=worker_id,
                    backend=active_backend,
                    lease_seconds=active_settings.worker_lease_seconds,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("index worker iteration failed")
                result = None

            if result is not None:
                logger.info(
                    "index job finished job_id=%s status=%s error=%s",
                    result.job_id,
                    result.status.value,
                    result.error,
                )
                if on_result is not None:
                    callback_result = on_result(result)
                    if callback_result is not None:
                        await callback_result
            ingestion_result = None
            try:
                # This also purges expired raw conversations when the LLM is
                # temporarily not configured; no batch is claimed in that case.
                ingestion_result = await run_ingestion_worker_once(
                    active_factory,
                    provider=active_llm_provider,
                    worker_id=worker_id,
                    lease_seconds=active_settings.worker_lease_seconds,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                # An unexpected persistence/provider error can contain model
                # output. Do not emit exception details that might include a
                # pasted conversation or derived personal data.
                logger.error("ingestion worker iteration failed")
                ingestion_result = None
            if ingestion_result is not None:
                logger.info(
                    "ingestion batch finished batch_id=%s status=%s generated=%s error=%s",
                    ingestion_result.batch_id,
                    ingestion_result.status.value,
                    ingestion_result.generated_count,
                    ingestion_result.error,
                )
            if result is not None or ingestion_result is not None:
                continue

            await wait_for_stop(active_stop_event, active_settings.worker_poll_interval_seconds)
    finally:
        logger.info("index worker stopping worker_id=%s", worker_id)
        if owns_factory:
            bind = active_factory.kw.get("bind")
            if isinstance(bind, AsyncEngine):
                await bind.dispose()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Nairag durable index worker")
    parser.add_argument(
        "--once",
        action="store_true",
        help="claim and process at most one job, then exit",
    )
    return parser.parse_args()


async def _run_once(settings: Settings) -> None:
    factory = create_session_factory(settings.database_url_with_password)
    try:
        result = await run_index_worker_once(
            factory,
            worker_id=resolve_worker_id(settings),
            backend=create_index_backend(settings),
            lease_seconds=settings.worker_lease_seconds,
        )
        if result is not None:
            logger.info(
                "index job finished job_id=%s status=%s error=%s",
                result.job_id,
                result.status.value,
                result.error,
            )
            # Keep --once limited to one claimed job while still applying the
            # raw-chat retention cleanup that does not claim a batch.
            await run_ingestion_worker_once(
                factory,
                provider=None,
                worker_id=resolve_worker_id(settings),
                lease_seconds=settings.worker_lease_seconds,
            )
        else:
            ingestion_result = await run_ingestion_worker_once(
                factory,
                provider=create_llm_provider(settings),
                worker_id=resolve_worker_id(settings),
                lease_seconds=settings.worker_lease_seconds,
            )
            if ingestion_result is not None:
                logger.info(
                    "ingestion batch finished batch_id=%s status=%s generated=%s error=%s",
                    ingestion_result.batch_id,
                    ingestion_result.status.value,
                    ingestion_result.generated_count,
                    ingestion_result.error,
                )
    finally:
        bind = factory.kw.get("bind")
        if isinstance(bind, AsyncEngine):
            await bind.dispose()


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = _parse_args()
    settings = get_settings()
    if args.once:
        asyncio.run(_run_once(settings))
    else:
        asyncio.run(run_worker(settings=settings))


if __name__ == "__main__":
    main()
