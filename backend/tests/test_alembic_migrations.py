from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    UniqueConstraint,
    Uuid,
    create_engine,
    inspect,
    text,
)

from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_alembic_revision_ids_fit_version_table_column() -> None:
    versions_dir = Path(__file__).parents[1] / "alembic" / "versions"
    revision_ids = [
        match.group(1)
        for path in sorted(versions_dir.glob("*.py"))
        if (
            match := re.search(
                r'^revision: str = "([^"]+)"$',
                path.read_text(),
                flags=re.MULTILINE,
            )
        )
    ]

    assert revision_ids
    assert all(len(revision_id) <= 32 for revision_id in revision_ids), revision_ids


def test_knowledge_taxonomy_migration_seeds_the_existing_choices() -> None:
    migration_path = (
        Path(__file__).parents[1] / "alembic" / "versions" / "0015_knowledge_taxonomy.py"
    )
    specification = importlib.util.spec_from_file_location(
        "knowledge_taxonomy_migration", migration_path
    )
    assert specification is not None and specification.loader is not None
    migration = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(migration)

    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                migration.upgrade()
            assert "knowledge_taxonomy_option" in inspect(connection).get_table_names()
            values = connection.execute(
                text(
                    "SELECT field_key, value, sort_order "
                    "FROM knowledge_taxonomy_option ORDER BY field_key, sort_order"
                )
            ).all()
    finally:
        engine.dispose()

    seeded_values = {(field_key, value) for field_key, value, _sort_order in values}
    assert ("question_types", "功能故障类") in seeded_values
    assert ("business_objects", "对应平台使用说明书") in seeded_values
    assert ("purposes", "企业微信咨询") in seeded_values
    assert ("customer_types", "个人客户") in seeded_values


def test_review_history_access_migration_supports_upgrade_and_downgrade() -> None:
    migration_path = (
        Path(__file__).parents[1] / "alembic" / "versions" / "0016_review_history_access.py"
    )
    specification = importlib.util.spec_from_file_location(
        "review_history_access_migration", migration_path
    )
    assert specification is not None and specification.loader is not None
    migration = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(migration)

    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            metadata = MetaData()
            Table(
                "user_account",
                metadata,
                Column("id", Uuid(), primary_key=True),
            ).create(connection)
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                migration.upgrade()
            inspector = inspect(connection)
            assert "review_history_access" in inspector.get_table_names()
            assert len(inspector.get_foreign_keys("review_history_access")) == 3
            with Operations.context(context):
                migration.downgrade()
            assert "review_history_access" not in inspect(connection).get_table_names()
    finally:
        engine.dispose()


def test_append_review_decision_events_migration_supports_upgrade_and_downgrade() -> None:
    migration_path = (
        Path(__file__).parents[1] / "alembic" / "versions" / "0017_append_review_decision_events.py"
    )
    specification = importlib.util.spec_from_file_location(
        "append_review_decision_events_migration", migration_path
    )
    assert specification is not None and specification.loader is not None
    migration = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(migration)

    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys = ON")
            metadata = MetaData()
            Table("review_submission", metadata, Column("id", Uuid(), primary_key=True))
            Table("knowledge_base", metadata, Column("id", Uuid(), primary_key=True))
            Table(
                "review_submission_target",
                metadata,
                Column("review_submission_id", Uuid(), nullable=False),
                Column("knowledge_base_id", Uuid(), nullable=False),
                Column("status", String(32), nullable=False),
                CheckConstraint(
                    "status IN ('pending_review', 'approved', 'rejected', 'indexing', "
                    "'published', 'index_failed')",
                    name="review_target_status",
                ),
                PrimaryKeyConstraint("review_submission_id", "knowledge_base_id"),
                ForeignKeyConstraint(["review_submission_id"], ["review_submission.id"]),
                ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_base.id"]),
            )
            Table(
                "review_decision",
                metadata,
                Column("id", Uuid(), primary_key=True),
                Column("review_submission_id", Uuid(), nullable=False),
                Column("knowledge_base_id", Uuid(), nullable=False),
                Column("decided_at", DateTime(timezone=True), nullable=False),
                UniqueConstraint(
                    "review_submission_id",
                    "knowledge_base_id",
                    name="uq_review_decision_submission_target",
                ),
                ForeignKeyConstraint(
                    ["review_submission_id", "knowledge_base_id"],
                    [
                        "review_submission_target.review_submission_id",
                        "review_submission_target.knowledge_base_id",
                    ],
                    name="fk_review_decision_submission_target",
                    ondelete="RESTRICT",
                ),
            )
            metadata.create_all(connection)
            submission_id = uuid4().hex
            knowledge_base_id = uuid4().hex
            decision_id = uuid4().hex
            connection.execute(
                text("INSERT INTO review_submission (id) VALUES (:id)"),
                {"id": submission_id},
            )
            connection.execute(
                text("INSERT INTO knowledge_base (id) VALUES (:id)"),
                {"id": knowledge_base_id},
            )
            connection.execute(
                text(
                    "INSERT INTO review_submission_target "
                    "(review_submission_id, knowledge_base_id, status) "
                    "VALUES (:submission_id, :knowledge_base_id, 'published')"
                ),
                {"submission_id": submission_id, "knowledge_base_id": knowledge_base_id},
            )
            connection.execute(
                text(
                    "INSERT INTO review_decision "
                    "(id, review_submission_id, knowledge_base_id, decided_at) "
                    "VALUES (:id, :submission_id, :knowledge_base_id, CURRENT_TIMESTAMP)"
                ),
                {
                    "id": decision_id,
                    "submission_id": submission_id,
                    "knowledge_base_id": knowledge_base_id,
                },
            )
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                migration.upgrade()

            inspector = inspect(connection)
            assert not any(
                constraint["name"] == "uq_review_decision_submission_target"
                for constraint in inspector.get_unique_constraints("review_decision")
            )
            assert any(
                index["name"] == "ix_review_decision_target_history"
                for index in inspector.get_indexes("review_decision")
            )
            status_check = next(
                constraint
                for constraint in inspector.get_check_constraints("review_submission_target")
                if constraint["name"] == "review_target_status"
            )
            assert "withdrawn" in status_check["sqltext"]
            assert (
                connection.execute(
                    text("SELECT COUNT(*) FROM review_decision WHERE id = :id"),
                    {"id": decision_id},
                ).scalar_one()
                == 1
            )
            assert any(
                foreign_key["name"] == "fk_review_decision_submission_target"
                for foreign_key in inspector.get_foreign_keys("review_decision")
            )

            with Operations.context(context):
                migration.downgrade()
            inspector = inspect(connection)
            assert any(
                constraint["name"] == "uq_review_decision_submission_target"
                for constraint in inspector.get_unique_constraints("review_decision")
            )
            assert not any(
                index["name"] == "ix_review_decision_target_history"
                for index in inspector.get_indexes("review_decision")
            )
    finally:
        engine.dispose()
