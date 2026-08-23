"""Clear Nairag knowledge data and its derived Milvus collections.

This is an operational reset tool, not an application endpoint. It keeps
accounts, permissions, knowledge-base definitions, and audit events intact.
Run it from the API image so it can use the same database, secret, and storage
configuration as the running application.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  # Register all model metadata.
from app.core.config import Settings, get_settings
from app.models.intelligent_ingestion import IntelligentIngestionBatch, KnowledgeDraft
from app.models.knowledge_content import (
    Child,
    ChildKnowledgeBasePublication,
    ChildRevision,
    ChildRevisionQuestionVariant,
    EvidenceAttachment,
    HelpfulFeedbackEvent,
    IndexJob,
    Parent,
    ParentLexicalRule,
    ParentRevision,
    ReviewDecision,
    ReviewSubmission,
    ReviewSubmissionTarget,
    SearchEvent,
    SearchResultItem,
    WebLink,
)
from app.services.attachment_storage import create_attachment_storage

# Delete dependants before the immutable content identities they reference.
CONTENT_TABLES: tuple[type[object], ...] = (
    HelpfulFeedbackEvent,
    SearchResultItem,
    SearchEvent,
    IndexJob,
    ReviewDecision,
    ChildKnowledgeBasePublication,
    ReviewSubmissionTarget,
    EvidenceAttachment,
    WebLink,
    ChildRevisionQuestionVariant,
    KnowledgeDraft,
    IntelligentIngestionBatch,
    ReviewSubmission,
    ChildRevision,
    Child,
    ParentLexicalRule,
    ParentRevision,
    Parent,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "删除 Nairag 全部知识内容、附件对象和应用创建的 Milvus 集合；"
            "账号、权限、知识库定义和审计日志会保留。"
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只统计待删除内容，不执行删除。",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="确认执行删除；没有此参数时脚本只展示预览并退出。",
    )
    parser.add_argument(
        "--all-milvus-collections",
        action="store_true",
        help="删除 Milvus 中所有集合，而不只删除名称以 nairag_ 开头的集合。",
    )
    parser.add_argument(
        "--skip-milvus",
        action="store_true",
        help="跳过 Milvus 操作；仅在 Milvus 暂不可用时使用。",
    )
    parser.add_argument(
        "--milvus-url",
        default=None,
        help="覆盖配置中的 Milvus URL，例如 http://milvus:19530。",
    )
    parser.add_argument(
        "--milvus-db-name",
        default="_default",
        help="Milvus 数据库名称，默认是 _default。",
    )
    parser.add_argument(
        "--preserve-attachment-objects",
        action="store_true",
        help="只删除附件数据库记录，不删除本地/MinIO 中的附件对象。",
    )
    return parser.parse_args()


async def count_rows(session: AsyncSession, model: type[object]) -> int:
    count = await session.scalar(select(func.count()).select_from(model))
    return int(count or 0)


async def collect_database_preview(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[dict[str, int], list[str]]:
    async with session_factory() as session:
        counts = {
            model.__tablename__: await count_rows(session, model)  # type: ignore[attr-defined]
            for model in CONTENT_TABLES
        }
        attachment_keys = list(
            (
                await session.scalars(
                    select(EvidenceAttachment.storage_key).order_by(EvidenceAttachment.id)
                )
            ).all()
        )
    return counts, attachment_keys


async def delete_database_rows(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, int]:
    deleted: dict[str, int] = {}
    async with session_factory() as session:
        async with session.begin():
            for model in CONTENT_TABLES:
                result = await session.execute(delete(model))
                deleted[model.__tablename__] = int(result.rowcount or 0)  # type: ignore[attr-defined]
    return deleted


async def delete_attachment_objects(settings: Settings, storage_keys: Sequence[str]) -> None:
    if not storage_keys:
        return
    storage = create_attachment_storage(settings)
    await storage.initialize()
    failures: list[str] = []
    for storage_key in storage_keys:
        try:
            await storage.delete_object(storage_key)
        except Exception as exc:  # noqa: BLE001 - report every failed object and continue.
            failures.append(f"{storage_key}: {exc}")
    if failures:
        details = "\n".join(f"  - {failure}" for failure in failures)
        raise RuntimeError(f"附件对象删除失败：\n{details}")


async def milvus_post(
    client: httpx.AsyncClient,
    path: str,
    *,
    payload: dict[str, object],
) -> dict[str, object]:
    response = await client.post(path, json=payload)
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise RuntimeError(f"Milvus 返回格式无效：{path}")
    if body.get("code") not in (None, 0, "0"):
        raise RuntimeError(f"Milvus 请求失败：{body.get('message', 'unknown error')}")
    return body


async def list_milvus_collections(
    settings: Settings,
    *,
    milvus_url: str,
    db_name: str,
) -> list[str]:
    headers = {"Authorization": f"Bearer {settings.milvus_token}"} if settings.milvus_token else {}
    async with httpx.AsyncClient(
        base_url=milvus_url.rstrip("/"),
        headers=headers,
        timeout=settings.embedding_timeout_seconds,
    ) as client:
        body = await milvus_post(
            client,
            "/v2/vectordb/collections/list",
            payload={"dbName": db_name},
        )
    data = body.get("data")
    if not isinstance(data, list) or any(not isinstance(name, str) for name in data):
        raise RuntimeError("Milvus 集合列表格式无效")
    return list(data)


async def drop_milvus_collections(
    settings: Settings,
    *,
    milvus_url: str,
    db_name: str,
    collection_names: Sequence[str],
) -> None:
    if not collection_names:
        return
    headers = {"Authorization": f"Bearer {settings.milvus_token}"} if settings.milvus_token else {}
    failures: list[str] = []
    async with httpx.AsyncClient(
        base_url=milvus_url.rstrip("/"),
        headers=headers,
        timeout=settings.embedding_timeout_seconds,
    ) as client:
        for collection_name in collection_names:
            try:
                await milvus_post(
                    client,
                    "/v2/vectordb/collections/drop",
                    payload={"dbName": db_name, "collectionName": collection_name},
                )
            except Exception as exc:  # noqa: BLE001 - attempt every collection.
                failures.append(f"{collection_name}: {exc}")
    if failures:
        details = "\n".join(f"  - {failure}" for failure in failures)
        raise RuntimeError(f"Milvus 集合删除失败：\n{details}")


def print_preview(
    counts: dict[str, int],
    attachment_keys: Sequence[str],
    milvus_collections: Sequence[str],
    *,
    preserve_attachment_objects: bool,
) -> None:
    print("将删除以下 PostgreSQL 知识内容：")
    for table_name, count in counts.items():
        print(f"  {table_name}: {count}")
    attachment_action = "保留对象" if preserve_attachment_objects else "删除对象"
    print(f"  附件对象: {len(attachment_keys)}（{attachment_action}）")
    print(f"将删除以下 Milvus 集合（{len(milvus_collections)} 个）：")
    for collection_name in milvus_collections:
        print(f"  {collection_name}")


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database_url_with_password, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        counts, attachment_keys = await collect_database_preview(session_factory)

        all_collections: list[str] = []
        if not args.skip_milvus:
            milvus_url = args.milvus_url or settings.milvus_url
            if not milvus_url:
                raise RuntimeError(
                    "未配置 MILVUS_URL；如需只清理数据库，请显式使用 --skip-milvus。"
                )
            all_collections = await list_milvus_collections(
                settings,
                milvus_url=milvus_url,
                db_name=args.milvus_db_name,
            )
        milvus_collections = (
            all_collections
            if args.all_milvus_collections
            else [name for name in all_collections if name.startswith("nairag_")]
        )
        print_preview(
            counts,
            attachment_keys,
            milvus_collections,
            preserve_attachment_objects=args.preserve_attachment_objects,
        )

        if args.dry_run:
            print("\n这是 dry-run，没有执行删除。")
            return 0
        if not args.yes:
            print("\n已拒绝执行：请确认服务已停止写入，并重新添加 --yes。")
            return 2

        deleted = await delete_database_rows(session_factory)
        print("PostgreSQL 内容已删除：")
        for table_name, count in deleted.items():
            print(f"  {table_name}: {count}")

        if not args.preserve_attachment_objects:
            await delete_attachment_objects(settings, attachment_keys)
            print(f"附件对象已删除：{len(attachment_keys)}")

        if not args.skip_milvus:
            assert milvus_url is not None
            await drop_milvus_collections(
                settings,
                milvus_url=milvus_url,
                db_name=args.milvus_db_name,
                collection_names=milvus_collections,
            )
            print(f"Milvus 集合已删除：{len(milvus_collections)}")
        else:
            print("已跳过 Milvus 集合删除。")
        return 0
    finally:
        await engine.dispose()


def main() -> int:
    try:
        return asyncio.run(run(parse_args()))
    except (httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
        print(f"清理失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
