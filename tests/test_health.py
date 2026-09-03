import os
import tempfile
from typing import Generator

import pytest

from ode.db import init_database
from ode.health import Health, get_health


@pytest.fixture
def initialized_db() -> Generator[str, None, None]:
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        path = f.name
    init_database(path)
    yield path
    if os.path.exists(path):
        os.unlink(path)


def test_get_health_reports_ok_for_initialized_database(initialized_db: str) -> None:
    health = get_health(initialized_db)
    assert isinstance(health, Health)
    assert health.database == "ok"
    assert "users" in health.tables


def test_get_health_reports_missing_for_nonexistent_path() -> None:
    health = get_health("/tmp/does-not-exist-ode.sqlite")
    assert health.database == "missing"
    assert health.tables == []
