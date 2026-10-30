"""Contract the repository-root inputs mutmut copies into its sandbox.

mutmut resolves ``repo_root`` to ``mutants/`` at run time: it copies the
source, the test suite, and every path in ``[tool.mutmut] also_copy`` into
that directory, then runs pytest with ``mutants/`` as the working directory.
The workflow-contract modules read repository configuration relative to that
root, so an input missing from ``also_copy`` fails the baseline before mutmut
writes any statistics.

These assertions therefore inspect the tree pytest is actually running from,
which is the real checkout under ``make test`` and the copied sandbox under
``mutmut run``. They are deliberately not skipped in the sandbox: a sandbox
without the inputs is exactly the failure this contract exists to catch.
"""

import tomllib
import typing as typ
from pathlib import Path

type SandboxInput = tuple[str, str]

#: Every audited repository-root input and the contract test that reads it.
#: ``mutants/`` receives these paths because they are declared in
#: ``[tool.mutmut] also_copy`` in pyproject.toml.
INPUT_CONSUMERS: tuple[SandboxInput, ...] = (
    ("docs/", "tests/unit/test_users_guide.py reads docs/users-guide.md"),
    (
        "scripts/",
        (
            "the lint-environment contract reads "
            "scripts/publish-check/bin/cargo, and the cuprum and release "
            "contracts read the scripts' PEP 723 locks"
        ),
    ),
    ("uv.lock", "the cuprum-selection contract checks it against pyproject.toml"),
    (
        ".github/",
        (
            "workflow contracts read .github/workflows/*.yml, "
            ".github/actions/*/action.yml, and .github/cv005.toml"
        ),
    ),
    (
        "Makefile",
        (
            "the lint-target, lint-environment, pylint-tier, CV-005 wiring, "
            "source-discovery, and duplication-toolchain contracts run "
            "`make -n`"
        ),
    ),
    (".gitignore", "the lint-environment contract reads its ignore entries"),
)

_WORKFLOWS = Path(".github") / "workflows"


def _normalized(path: str) -> str:
    """Return a path without its trailing separator."""
    return path.rstrip("/")


def _mapping(value: object, *, subject: str) -> dict[str, object]:
    """Return a TOML table, naming the unexpected `subject` on failure."""
    assert isinstance(value, dict), f"Expected {subject} to be a TOML table."
    return typ.cast("dict[str, object]", value)


def _also_copy(repo_root: Path) -> frozenset[str]:
    """Return the normalised ``[tool.mutmut] also_copy`` entries."""
    configuration = tomllib.loads(
        (repo_root / "pyproject.toml").read_text(encoding="utf-8")
    )
    tool = _mapping(configuration.get("tool"), subject="tool configuration")
    mutmut = _mapping(tool.get("mutmut"), subject="mutmut configuration")
    entries = mutmut.get("also_copy")
    assert isinstance(entries, list), "mutmut also_copy must be a TOML array."
    assert all(isinstance(entry, str) for entry in entries), (
        "Every mutmut also_copy entry must be a string."
    )
    return frozenset(_normalized(entry) for entry in typ.cast("list[str]", entries))


def _missing_from_also_copy(repo_root: Path) -> list[str]:
    """Return audited inputs absent from ``also_copy``, with their consumers."""
    declared = _also_copy(repo_root)
    return [
        f"{path} ({consumer})"
        for path, consumer in INPUT_CONSUMERS
        if _normalized(path) not in declared
    ]


def _missing_from_tree(repo_root: Path) -> list[str]:
    """Return audited inputs absent under ``repo_root``, with their consumers."""
    return [
        f"{path} ({consumer})"
        for path, consumer in INPUT_CONSUMERS
        if not (repo_root / _normalized(path)).exists()
    ]


def test_also_copy_declares_every_audited_input(repo_root: Path) -> None:
    """Every root input a contract test reads must be in mutmut's copy list."""
    missing = _missing_from_also_copy(repo_root)
    assert not missing, (
        "mutmut cannot supply these consumers because their inputs are absent "
        "from [tool.mutmut] also_copy in pyproject.toml: " + "; ".join(missing)
    )


def test_declared_inputs_exist_in_this_tree(repo_root: Path) -> None:
    """Every declared input must exist where pytest is running from."""
    missing = _missing_from_tree(repo_root)
    assert not missing, (
        f"These sandbox inputs are missing from {repo_root}; declare them in "
        "[tool.mutmut] also_copy in pyproject.toml: " + "; ".join(missing)
    )


def test_workflow_tree_carries_a_workflow(repo_root: Path) -> None:
    """The copied ``.github/workflows`` tree must hold a real workflow."""
    workflows = sorted((repo_root / _WORKFLOWS).glob("*.yml"))
    assert workflows, (
        f"{_WORKFLOWS}/ holds no *.yml workflow under {repo_root}; the "
        "workflow contracts need the repository configuration copied into "
        "the sandbox"
    )
