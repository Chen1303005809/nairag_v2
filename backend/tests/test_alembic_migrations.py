from __future__ import annotations

import re
from pathlib import Path


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
