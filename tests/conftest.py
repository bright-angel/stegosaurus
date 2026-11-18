"""Fixtures shared by the test suite."""

from __future__ import annotations

import _upstream
import pytest
import support

from stegosaurus import analyse


@pytest.fixture(scope="session")
def oracle():
    """Upstream reference implementation, or skip if it is not checked out."""
    loaded = _upstream.load_oracle()
    if loaded is None:
        pytest.skip(
            "upstream reference implementation not found: clone "
            "https://github.com/AngelKitty/stegosaurus into 'upstream-ref/' or set "
            "$STEGOSAURUS_UPSTREAM"
        )
    return loaded


@pytest.fixture
def carrier(tmp_path):
    """Small checked-in carrier script (AST: functions, loop, call)."""
    return support.copy_data_carrier(tmp_path, "carrier.py")


@pytest.fixture
def roomy(tmp_path):
    """Checked-in carrier with a higher dead-zone density."""
    return support.copy_data_carrier(tmp_path, "roomy.py")


@pytest.fixture
def big_carrier(tmp_path):
    """Generated carrier with room for a few hundred payload bytes."""
    return support.make_synthetic_carrier(tmp_path, loops=40)


@pytest.fixture
def capacity_of():
    """``capacity_of(path) -> int`` helper."""

    def _capacity(path, **kwargs):
        return analyse(path, **kwargs).capacity

    return _capacity
