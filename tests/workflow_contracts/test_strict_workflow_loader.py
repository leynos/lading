"""Contract tests for the strict workflow loader.

The loader refuses a mapping key declared twice instead of keeping the last
value, and keeps distinct keys apart.
"""

import pytest

from tests.workflow_contracts.strict_workflow_loader import DuplicateKeyError, _load


def test_the_loader_refuses_a_repeated_key() -> None:
    """Fail on `runs-on` declared twice rather than keeping the second label."""
    doubled = "jobs:\n  build:\n    runs-on: self-hosted\n    runs-on: ubuntu-latest\n"
    with pytest.raises(DuplicateKeyError, match="runs-on"):
        _load(doubled)


def test_the_loader_keeps_distinct_keys() -> None:
    """Read a workflow whose keys are all different."""
    parsed = _load("jobs:\n  a:\n    runs-on: x\n  b:\n    runs-on: y\n")
    assert parsed == {"jobs": {"a": {"runs-on": "x"}, "b": {"runs-on": "y"}}}, parsed
