"""Make the gate modules importable and keep this suite off the app run.

The gate wrapper runs on an explicitly selected CPython 3.14 with pinned
tooling dependencies (``make duplication-test``). The application suite runs
on the project virtualenv, a different interpreter with a different dependency
set, and it recurses the whole repository because neither ``testpaths`` nor
``norecursedirs`` is configured. These modules are therefore not collectable
there: the guard below asks that run to skip the directory instead of failing
while importing a module it cannot interpret.
"""

import importlib.util
import sys
from pathlib import Path

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

    The suite imports the pinned cyclopts and tomlkit the wrapper declares,
    and the wrapper itself requires Python 3.14.
    """
    if sys.version_info < (3, 14):
        return False
    return all(
        _module_is_importable(name) for name in ("cyclopts", "hypothesis", "tomlkit")
    )


def _module_is_importable(name: str) -> bool:
    """Report whether ``name`` resolves without importing it."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):  # pragma: no cover - defensive
        return False


if not _tooling_environment_is_available():  # pragma: no cover - app venv only
    collect_ignore_glob = ["*.py"]
