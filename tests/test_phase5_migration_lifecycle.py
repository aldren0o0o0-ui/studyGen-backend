"""
tests/test_phase5_migration_lifecycle.py
Alembic migration lifecycle test — verifies the Phase 5 migration file exists, is syntactically
valid, and that the revision graph has exactly one head.

Note: Full up/down cycle is covered by test_migrations.py::test_alembic_lifecycle.
This test ensures Phase 5 migration integrity without touching the shared test DB.
"""
from __future__ import annotations

import os
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory


def test_phase5_migration_file_exists_and_is_head():
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    alembic_ini_path = os.path.join(backend_dir, "alembic.ini")

    if not os.path.exists(alembic_ini_path):
        pytest.skip("alembic.ini not found in backend directory.")

    alembic_cfg = Config(alembic_ini_path)
    script = ScriptDirectory.from_config(alembic_cfg)
    revisions = list(script.walk_revisions())

    # Verify we have at least one revision
    assert len(revisions) >= 1, "No migration revisions found."

    # Verify exactly one head (no branching in migration graph)
    heads = list(script.get_heads())
    assert len(heads) == 1, f"Expected exactly 1 migration head, found: {heads}"

    # Verify Phase 5 revision exists in the chain
    rev_ids = {r.revision for r in revisions}
    # Phase 5 revision ID defined in the migration file
    phase5_rev_id = "b2c3d4e5f6a7"
    assert phase5_rev_id in rev_ids, (
        f"Phase 5 revision '{phase5_rev_id}' not found in migration chain. "
        f"Available revisions: {sorted(rev_ids)}"
    )

    # Phase 5 remains in the linear chain; later additive revisions are the head.
    current_head = script.get_current_head()
    assert current_head == "0016_phase12a_1_session_security", (
        "Phase 12A.1 revision '0016_phase12a_1_session_security' is not the current head. "
        f"Current head: {current_head}"
    )

    # Verify migration chain is linear (no merge points)
    for rev in revisions:
        if rev.down_revision is not None:
            assert isinstance(rev.down_revision, str), (
                f"Revision {rev.revision} has a tuple down_revision (merge point): {rev.down_revision}"
            )
