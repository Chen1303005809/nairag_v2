from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

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
