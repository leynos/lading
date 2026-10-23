"""Make the gate modules importable and skip this suite when tooling is missing.

The gate wrapper runs on an explicitly selected CPython 3.14 with pinned
tooling dependencies (``make duplication-test``). The application suite runs
on the project virtualenv, a different interpreter with a different dependency
set, and it recurses the whole repository because neither ``testpaths`` nor
``norecursedirs`` is configured. If that interpreter lacks a tooling dependency,
the guard below asks pytest to skip these files instead of failing while
importing a module it cannot interpret. When the dependencies are available,
the application suite may collect these tests; ``make duplication-test`` runs
them with their pinned tooling in either case.
"""

import importlib.util
import os
import sys
from pathlib import Path

import pytest

SCRIPT_DIRECTORY = Path(__file__).resolve().parents[1]

# The directory must be appended, not prepended. Neither `tests/` nor
# `scripts/tests/` is a namespace package here, so `scripts/tests` is a
# distinct top-level module name; putting `scripts/` first would shadow the
# repository root's entries for the application suite. Reachability of the
# gate modules is guaranteed by this entry alone.
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.append(str(SCRIPT_DIRECTORY))


def _tooling_environment_is_available() -> bool:
    """Report whether this interpreter can run the duplication-gate suite.

    The suite imports the pinned cyclopts and tomlkit the wrapper declares
    and needs syrupy for the shortcut snapshots. The interpreter floor is
    enforced by the wrapper's own ``requires-python`` rather than checked
    here, so only the dependency set can actually be missing.

    Returns
    -------
    bool
        ``True`` when every dependency the suite needs at import or fixture
        time is importable.
    """
    return all(
        _module_is_importable(name)
        for name in ("cyclopts", "hypothesis", "syrupy", "tomlkit")
    )


def _module_is_importable(name: str) -> bool:
    """Report whether ``name`` resolves without importing it."""
    try:
        return importlib.util.find_spec(name) is not None
    except ImportError, ValueError:  # pragma: no cover - defensive
        return False


if not _tooling_environment_is_available():  # pragma: no cover - app venv only
    collect_ignore_glob = ["*.py"]


@pytest.fixture(scope="session")
def real_nose_binary() -> str:
    """Resolve the pinned detector once for real-binary integration tests."""
    from duplication_gate_test_support import REPOSITORY_ROOT, detector

    settings = detector.load_settings(REPOSITORY_ROOT / "pyproject.toml")
    environment = detector.NoseEnvironment(
        repository_root=REPOSITORY_ROOT,
        binary_override=os.environ.get("NOSE_BIN"),
        search_directories=(
            str(REPOSITORY_ROOT / detector.DEFAULT_NOSE_RELATIVE_PATH.parent),
            *(os.environ["PATH"].split(os.pathsep) if os.environ.get("PATH") else ()),
        ),
    )
    try:
        return detector.resolve_binary(
            settings,
            environment=environment,
            binary_finder=detector.discover_binary,
            runner=detector.repository_runner(REPOSITORY_ROOT),
        )
    except detector.GateExecutionError as error:  # pragma: no cover
        pytest.skip(str(error))
        raise  # Unreachable; keeps the fixture's return paths explicit.
