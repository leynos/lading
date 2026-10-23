"""Contracts for the real-detector test fixture."""

import collections.abc as cabc
import importlib.util
import typing as typ
from pathlib import Path

import pytest
from duplication_gate_test_support import detector


def _real_nose_binary_fixture() -> cabc.Callable[[], str]:
    """Load the fixture body so its skip and failure paths can be exercised."""
    path = Path(__file__).with_name("conftest.py")
    spec = importlib.util.spec_from_file_location(
        "duplication_gate_test_conftest", path
    )
    assert spec is not None, "conftest.py should have an importable module spec"
    assert spec.loader is not None, "conftest.py module spec should have a loader"

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fixture = module.real_nose_binary
    return typ.cast("cabc.Callable[[], str]", fixture.__wrapped__)


def test_real_nose_binary_skips_when_detector_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing optional tooling skips the real-binary integration tests."""
    monkeypatch.delenv("NOSE_BIN", raising=False)
    monkeypatch.setattr(detector, "discover_binary", lambda _environment: None)

    with pytest.raises(pytest.skip.Exception):
        _real_nose_binary_fixture()()


@pytest.mark.parametrize(
    ("binary_override", "discovered_binary"),
    [(None, "/tools/nose"), ("/explicit/nose", None)],
    ids=["discovered-binary", "explicit-override"],
)
def test_real_nose_binary_fails_when_binary_version_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
    binary_override: str | None,
    discovered_binary: str | None,
) -> None:
    """An invalid installed or explicitly selected detector fails, not skips."""
    if binary_override is None:
        monkeypatch.delenv("NOSE_BIN", raising=False)
    else:
        monkeypatch.setenv("NOSE_BIN", binary_override)
    monkeypatch.setattr(
        detector, "discover_binary", lambda _environment: discovered_binary
    )

    def report_wrong_version(_command: cabc.Sequence[str]) -> str:
        return "nose 0.19.0\n"

    monkeypatch.setattr(
        detector,
        "repository_runner",
        lambda _root: report_wrong_version,
    )

    try:
        with pytest.raises(
            detector.GateExecutionError,
            match=r"reports 'nose 0\.19\.0'",
        ):
            _real_nose_binary_fixture()()
    except pytest.skip.Exception as error:
        pytest.fail(f"an invalid installed detector was skipped: {error}")
