"""Shared pytest fixtures and hooks for the Picurate test suite."""
import pytest
import core.db.catalog as _cat_mod


@pytest.fixture(autouse=True)
def _reset_thread_local_db():
    """Clear the per-thread SQLite connection cache before every test.

    get_connection() caches connections by thread without checking which
    database path is currently active.  Without this reset, a test that
    opens catalog A can leave _local.conn pointing to A, and the next
    test that opens catalog B (a different tmp_path) will silently reuse
    the stale connection to A.
    """
    _cat_mod._local.__dict__.clear()
    yield
    _cat_mod._local.__dict__.clear()
