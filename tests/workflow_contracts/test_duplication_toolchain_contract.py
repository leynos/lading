"""Contract tests for the pinned nose duplication-gate toolchain.

The detector version is declared in three places: ``NOSE_VERSION`` in the
Makefile, the ``NOSE_VERSION`` environment in ``.github/workflows/ci.yml``, and
``[tool.nose].version`` in ``pyproject.toml``. They must agree, because the
gate verifies the installed binary against ``[tool.nose].version`` while the
Makefile and CI are what install it. Drift between them produces a detector
that installs cleanly and then fails its own version check.

The other pin that must not drift is the installation strategy: a missing
prebuilt release is a provisioning failure, never permission to compile the
detector from source or to accept a binary from an unapproved source.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tomllib
import typing as typ
from pathlib import Path

import pytest

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion

REPO_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE_PATH = REPO_ROOT / "Makefile"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
CI_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"

MAKE_BINARY = shutil.which("make")

pytestmark = pytest.mark.skipif(
    MAKE_BINARY is None,
    reason="make executable not found on PATH",
)

#: The strategies that must never run for this gate.
FORBIDDEN_INSTALL_STRATEGIES = ("compile", "quick-install")


def _pyproject() -> dict[str, object]:
    """Load the repository pyproject as parsed TOML."""
    return tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))


def _nose_version() -> str:
    """Return the detector version pinned in ``[tool.nose]``."""
    tool = _pyproject()["tool"]
    assert isinstance(tool, dict), "The pyproject must expose a [tool] table."
    nose = tool["nose"]
    assert isinstance(nose, dict), "[tool.nose] must be a TOML table."
    version = nose["version"]
    assert isinstance(version, str), "[tool.nose].version must be a string."
    return version


def _makefile_text() -> str:
    """Return the Makefile source."""
    return MAKEFILE_PATH.read_text(encoding="utf-8")


def _make_variable(name: str, *, text: str | None = None) -> str | None:
    """Return the recursively expanded value of one Makefile variable.

    Parameters
    ----------
    name : str
        Makefile variable name.
    text : str | None
        Makefile source to search; defaults to the repository Makefile.

    Returns
    -------
    str | None
        The variable's assignment value, or ``None`` when it is undefined.
    """
    source = _makefile_text() if text is None else text
    match = re.search(
        rf"^{re.escape(name)}\s*\??=\s*(?P<value>.*)$",
        source,
        flags=re.MULTILINE,
    )
    return None if match is None else match.group("value").strip()


def _ci_nose_version() -> str | None:
    """Return the ``NOSE_VERSION`` env value declared in the CI workflow."""
    match = re.search(
        r'^\s*NOSE_VERSION:\s*"?(?P<version>[0-9][^"\s]*)"?\s*$',
        CI_WORKFLOW_PATH.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    return None if match is None else match.group("version")


def test_the_detector_version_is_pinned_in_pyproject() -> None:
    """The gate's own configuration names a concrete detector version."""
    version = _nose_version()

    assert re.fullmatch(r"\d+\.\d+\.\d+", version), (
        f"`[tool.nose].version` must be an exact release, not {version!r}."
    )


def test_every_declaration_site_agrees_on_the_detector_version() -> None:
    """The Makefile, CI, and pyproject declare one identical detector version."""
    version = _nose_version()
    makefile_version = _make_variable("NOSE_VERSION")
    ci_version = _ci_nose_version()

    assert makefile_version == version, (
        f"Makefile NOSE_VERSION is {makefile_version!r} but [tool.nose].version "
        f"is {version!r}; the installed binary would fail its own check."
    )
    assert ci_version == version, (
        f"ci.yml NOSE_VERSION is {ci_version!r} but [tool.nose].version is "
        f"{version!r}; CI would install a detector the gate rejects."
    )


def test_the_installer_refuses_unapproved_installation_strategies() -> None:
    """The recipe disables source compilation and third-party binary mirrors."""
    assert MAKE_BINARY is not None, "make executable not found on PATH"
    completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed target in the repository root.
        [MAKE_BINARY, "--dry-run", "--no-print-directory", "install-nose"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    recipe = completed.stdout

    assert "cargo-binstall" in recipe, "The installer must use cargo-binstall."
    strategies = _disabled_strategies(recipe)
    for strategy in FORBIDDEN_INSTALL_STRATEGIES:
        assert strategy in strategies, (
            f"The installer must disable the {strategy!r} strategy so a missing "
            "trusted binary fails provisioning instead of building from source."
        )


def _disabled_strategies(recipe: str) -> tuple[str, ...]:
    """Return the strategy names the install recipe disables."""
    match = re.search(r"--disable-strategies\s+(?P<value>\S+)", recipe)
    assert match is not None, "--disable-strategies is missing from the recipe."
    return tuple(match.group("value").split(","))


def test_the_installer_runs_non_interactively() -> None:
    """Installation must never wait for a prompt on a developer machine."""
    assert MAKE_BINARY is not None, "make executable not found on PATH"
    completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed target in the repository root.
        [MAKE_BINARY, "--dry-run", "--no-print-directory", "install-nose"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "--no-confirm" in completed.stdout, "The installer must not prompt."


def test_the_nose_install_is_not_a_floating_latest_installer() -> None:
    """The recipe requests an exact crate version rather than a floating ref."""
    assert MAKE_BINARY is not None, "make executable not found on PATH"
    completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed target in the repository root.
        [MAKE_BINARY, "--dry-run", "--no-print-directory", "install-nose"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    recipe = completed.stdout

    assert f"nose-cli@{_nose_version()}" in recipe, (
        "The installer must request the pinned detector release."
    )
    assert "corca-ai/nose" in recipe, "The crate must come from its upstream repo."
    assert re.search(r"nose-cli@(latest|main|master)\b", recipe) is None, (
        "A floating version would silently change the gate between runs."
    )


def test_the_gate_is_wired_into_the_lint_target() -> None:
    """The blocking gate must run as part of the canonical lint pipeline."""
    assert MAKE_BINARY is not None, "make executable not found on PATH"
    completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed target in the repository root.
        [MAKE_BINARY, "--dry-run", "--no-print-directory", "lint"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "scripts/duplication_gate.py check" in completed.stdout, (
        "`make lint` must run the duplication gate."
    )


def test_the_gate_does_not_require_the_application_environment(
    snapshot: SnapshotAssertion,
) -> None:
    """The gate wrapper resolves its own tooling rather than importing lading."""
    wrapper = (REPO_ROOT / "scripts" / "duplication_gate.py").read_text(
        encoding="utf-8"
    )
    metadata = re.search(r"# /// script\n(?P<body>.*?)# ///", wrapper, flags=re.DOTALL)
    assert metadata is not None, "The wrapper must declare PEP 723 inline metadata."
    body = metadata.group("body")
    declaration = tomllib.loads(
        "\n".join(line.removeprefix("# ") for line in body.splitlines())
    )

    # The interpreter floor and every pinned tooling dependency, locked
    # together. A snapshot states the whole declaration, so widening the gate's
    # dependencies or relaxing its interpreter has to be a reviewed change
    # rather than a string that happens to still be present.
    assert declaration == snapshot, (
        "The wrapper's PEP 723 declaration must name its interpreter floor and "
        "pin every tooling dependency it relies on."
    )

    for forbidden in ("lading", "cuprum", "msgspec"):
        assert forbidden not in body, (
            f"The gate must not depend on the application package {forbidden!r}."
        )


def test_the_helper_tests_are_not_collected_by_the_application_suite() -> None:
    """The tooling suite runs on its own interpreter, not the project venv."""
    source = (REPO_ROOT / "scripts" / "tests" / "conftest.py").read_text(
        encoding="utf-8"
    )

    assert "collect_ignore_glob" in source, (
        "The scripts tests must opt out of the application suite's collection, "
        "which runs on an interpreter without the pinned tooling."
    )


def test_configured_roots_exist_and_are_not_empty() -> None:
    """Every configured scan root must be a real, non-empty source tree."""
    tool = _pyproject()["tool"]
    assert isinstance(tool, dict), "The pyproject must expose a [tool] table."
    nose = tool["nose"]
    assert isinstance(nose, dict), "[tool.nose] must be a TOML table."
    roots = nose["roots"]
    assert isinstance(roots, list), "[tool.nose].roots must be a TOML array."
    assert roots, "[tool.nose].roots must not be empty."

    for root in roots:
        assert isinstance(root, str), "Every configured root must be a string path."
        directory = REPO_ROOT / root
        assert directory.is_dir(), f"Configured nose root {root!r} does not exist."
        assert any(directory.rglob("*.py")), (
            f"Configured nose root {root!r} contains no Python sources."
        )


def test_the_scan_never_targets_the_whole_checkout() -> None:
    """A repository-wide root would gate vendored and generated code."""
    tool = _pyproject()["tool"]
    assert isinstance(tool, dict), "The pyproject must expose a [tool] table."
    nose = tool["nose"]
    assert isinstance(nose, dict), "[tool.nose] must be a TOML table."
    roots = nose["roots"]
    assert isinstance(roots, list), "[tool.nose].roots must be a TOML array."

    for root in roots:
        assert root not in {".", "/", ""}, (
            "An unscoped root would silently gate the entire checkout."
        )


def test_the_ranking_bound_stays_bounded() -> None:
    """`top` must bound the adjudicated surface rather than disabling it."""
    tool = _pyproject()["tool"]
    assert isinstance(tool, dict), "The pyproject must expose a [tool] table."
    nose = tool["nose"]
    assert isinstance(nose, dict), "[tool.nose] must be a TOML table."

    assert nose["top"], "`top` must be set so the graded surface stays bounded."
    assert nose["top"] != 0, (
        "`top = 0` would disable the ranking bound rather than widen the view."
    )


def _nose_table() -> dict[str, object]:
    """Return the ``[tool.nose]`` table from the repository pyproject."""
    tool = _pyproject()["tool"]
    assert isinstance(tool, dict), "The pyproject must expose a [tool] table."
    nose = tool["nose"]
    assert isinstance(nose, dict), "[tool.nose] must be a TOML table."
    return nose


def test_configured_exclusions_actually_exclude_something() -> None:
    """Every exclusion glob must be able to match, at any depth.

    nose anchors ``--exclude`` globs to each ``--root`` rather than to the
    repository root. A repository-relative glob such as ``scripts/tests/**``
    therefore matches nothing when the root is ``scripts`` and silently
    excludes no files, so the gate reports families from a tree the
    configuration claims to have skipped. A glob that does not begin with
    ``**/`` only matches at the top level of a root, which is exactly the
    quietly-inert form.
    """
    nose = _nose_table()
    excludes = nose.get("exclude", [])
    assert isinstance(excludes, list), "[tool.nose].exclude must be a TOML array."

    for glob in excludes:
        assert isinstance(glob, str), "Every exclusion glob must be a string."
        assert glob.startswith("**/"), (
            f"Exclude glob {glob!r} is anchored to the top of a scan root and "
            "would exclude nothing below it; prefix it with `**/` to match at "
            "any depth under every configured root."
        )


def test_declared_exclusions_target_directories_that_exist() -> None:
    """A mistyped exclusion must not pass for a scoped scan."""
    nose = _nose_table()
    excludes = nose.get("exclude", [])
    assert isinstance(excludes, list), "[tool.nose].exclude must be a TOML array."
    roots = nose["roots"]
    assert isinstance(roots, list), "[tool.nose].roots must be a TOML array."

    for glob in excludes:
        assert isinstance(glob, str), "Every exclusion glob must be a string."
        literal = glob.removeprefix("**/").removesuffix("/**")
        assert literal, f"Exclude glob {glob!r} names no directory."
        matches = [
            path
            for root in roots
            if isinstance(root, str)
            for path in (REPO_ROOT / root).rglob(literal)
        ]
        assert matches, (
            f"Exclude glob {glob!r} names {literal!r}, which exists under no "
            "configured root; the gate would scan it rather than skip it."
        )


def test_the_gate_records_no_copied_exceptions() -> None:
    """Adoption starts from this repository's own adjudication, not upstream's."""
    tool = _pyproject()["tool"]
    assert isinstance(tool, dict), "The pyproject must expose a [tool] table."
    gate = tool.get("duplication_gate", {})
    assert isinstance(gate, dict), "[tool.duplication_gate] must be a TOML table."
    entries = gate.get("allow", [])
    assert isinstance(entries, list), (
        "[tool.duplication_gate].allow must be a TOML array."
    )

    for entry in entries:
        assert isinstance(entry, dict), "Every allow entry must be a TOML table."
        reason = entry.get("reason")
        assert isinstance(reason, str), (
            "Every recorded exception must carry a string reason."
        )
        assert reason.strip(), (
            "Every recorded exception must carry a reviewable reason."
        )
