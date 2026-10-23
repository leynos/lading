"""Contract tests for the pinned nose duplication-gate toolchain.

The detector version is declared in three places: ``NOSE_VERSION`` in the
Makefile, the ``NOSE_VERSION`` environment in ``.github/workflows/ci.yml``, and
``[tool.nose].version`` in ``pyproject.toml``. They must agree, because the
gate verifies the installed binary against ``[tool.nose].version`` while the
Makefile and CI are what install it. Drift between them produces a detector
that installs cleanly and then fails its own version check.

The detector's release archive and executable are pinned by digest for every
supported platform. A missing release, a mismatched digest, or an unsupported
platform is a provisioning failure rather than permission to accept other
bytes.
"""

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

NOSE_TARGETS = (
    "aarch64-apple-darwin",
    "x86_64-apple-darwin",
    "aarch64-unknown-linux-gnu",
    "x86_64-unknown-linux-gnu",
)


def _pyproject() -> dict[str, object]:
    """Load the repository pyproject as parsed TOML."""
    return tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))


def _table(value: object, *, subject: str) -> dict[str, object]:
    """Return one TOML table, naming the unexpected ``subject``.

    The cast is what makes a subscript usable: type checkers narrow
    ``isinstance(value, dict)`` from ``object`` to a synthetic top type whose
    ``__getitem__`` accepts ``Never``, so the narrowed value can be compared
    and passed on but never indexed by a literal key.

    Returns
    -------
    dict[str, object]
        The same table, widened to a mapping indexable by a literal key.
    """
    assert isinstance(value, dict), f"{subject} must be a TOML table."
    return typ.cast("dict[str, object]", value)


def _nose_version() -> str:
    """Return the detector version pinned in ``[tool.nose]``."""
    tool = _table(_pyproject()["tool"], subject="[tool]")
    nose = _table(tool["nose"], subject="[tool.nose]")
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


@pytest.fixture(scope="module")
def install_nose_recipe() -> str:
    """Return the expanded recipe used by all installer contract checks."""
    completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed target in the repository root.
        [MAKE_BINARY or "make", "--dry-run", "--no-print-directory", "install-nose"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def test_each_supported_release_and_executable_has_a_sha256_pin(
    install_nose_recipe: str,
) -> None:
    """Every supported artifact has immutable archive and executable pins."""
    for target in NOSE_TARGETS:
        variable_suffix = target.upper().replace("-", "_")
        for digest_kind in ("ARCHIVE", "BINARY"):
            variable = f"NOSE_{digest_kind}_SHA256_{variable_suffix}"
            digest = _make_variable(variable)
            assert digest is not None, f"{variable} must be declared."
            assert re.fullmatch(r"[0-9a-f]{64}", digest), (
                f"{variable} must contain a full lowercase SHA-256 digest."
            )

        assert target in install_nose_recipe, (
            f"The installer must support its pinned {target} release."
        )


def test_the_installer_downloads_and_checks_the_pinned_release(
    install_nose_recipe: str,
    snapshot: SnapshotAssertion,
) -> None:
    """The installer checks the official archive and executable bytes."""
    recipe = install_nose_recipe

    contract = {
        "official release URL is pinned": (
            "https://github.com/corca-ai/nose/releases/download/v0.20.0/" in recipe
        ),
        "curl fails on download errors": "curl -fsSL" in recipe,
        "redirects remain HTTPS": (
            "--proto '=https' --proto-redir '=https' --tlsv1.2" in recipe
        ),
        "Linux archive hashing is available": "sha256sum | cut" in recipe,
        "macOS archive hashing is available": "shasum -a 256 | cut" in recipe,
        "executable bytes are verified": "binary_checksum" in recipe,
        "cargo-binstall is absent": "cargo-binstall" not in recipe,
        "mutable crate references are absent": "nose-cli@" not in recipe,
    }
    assert contract == snapshot, (
        "The installer must use the pinned HTTPS release and verify both artifacts."
    )


def test_installer_fails_closed_on_missing_or_mismatched_bytes(
    install_nose_recipe: str,
    snapshot: SnapshotAssertion,
) -> None:
    """Missing downloads and digest mismatches must fail installation."""
    contract = {
        "shell exits on command failure": "set -eu" in install_nose_recipe,
        "download fails on HTTP errors": "curl -fsSL" in install_nose_recipe,
        "archive mismatch aborts": "archive digest mismatch" in install_nose_recipe,
        "executable mismatch aborts": (
            "executable digest mismatch" in install_nose_recipe
        ),
        "mismatch path exits nonzero": "exit 1" in install_nose_recipe,
    }
    assert contract == snapshot, (
        "Missing or mismatched release bytes must prevent detector installation."
    )


def test_ci_uses_the_shared_installer_and_invalidates_old_binary_caches() -> None:
    """CI must install through the digest verifier and bypass old cache entries."""
    workflow = CI_WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "run: make install-nose" in workflow, (
        "CI must use the shared nose installer."
    )
    assert "hashFiles('Makefile')" in workflow, (
        "The nose cache key must invalidate version-only cached binaries."
    )
    assert "BINSTALL_VERSION" not in workflow, (
        "CI must not retain a separate cargo-binstall version pin."
    )
    assert "cargo-binstall" not in workflow, (
        "CI must not install an unverified cargo-binstall binary."
    )


def test_the_helper_suite_selects_the_interpreter_it_requires() -> None:
    """`make duplication-test` must pin the interpreter its own gate declares.

    The suite's ``conftest.py`` ignores these modules when the application
    interpreter lacks a tooling dependency. ``make duplication-test`` provides
    those tools in an isolated environment, but it must also select the gate's
    declared Python baseline rather than inherit the ambient interpreter.
    """
    assert MAKE_BINARY is not None, "make executable not found on PATH"
    completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed target in the repository root.
        [MAKE_BINARY, "--dry-run", "--no-print-directory", "duplication-test"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    recipe = completed.stdout

    baseline = _make_variable("PYTHON_BASELINE")
    assert baseline is not None, "The Makefile must declare PYTHON_BASELINE."
    assert f"--python {baseline}" in recipe, (
        "`make duplication-test` must run the helper suite under the declared "
        f"baseline ({baseline}), not the ambient interpreter."
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


def test_helper_tests_are_skipped_when_tooling_dependencies_are_unavailable() -> None:
    """The app skips helper tests only when their tooling dependencies are missing."""
    source = (REPO_ROOT / "scripts" / "tests" / "conftest.py").read_text(
        encoding="utf-8"
    )
    conditional_skip = re.search(
        r"(?m)^if not _tooling_environment_is_available\(\):[^\n]*\n"
        r"[ \t]+collect_ignore_glob[ \t]*=",
        source,
    )

    assert conditional_skip is not None, (
        "The helper ignore assignment must be inside the unavailable-tooling guard."
    )


def test_configured_roots_exist_and_are_not_empty() -> None:
    """Every configured scan root must be a real, non-empty source tree."""
    tool = _table(_pyproject()["tool"], subject="[tool]")
    nose = _table(tool["nose"], subject="[tool.nose]")
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
    tool = _table(_pyproject()["tool"], subject="[tool]")
    nose = _table(tool["nose"], subject="[tool.nose]")
    roots = nose["roots"]
    assert isinstance(roots, list), "[tool.nose].roots must be a TOML array."

    for root in roots:
        assert root not in {".", "/", ""}, (
            "An unscoped root would silently gate the entire checkout."
        )


def test_the_ranking_bound_stays_bounded() -> None:
    """`top` must bound the adjudicated surface rather than disabling it."""
    tool = _table(_pyproject()["tool"], subject="[tool]")
    nose = _table(tool["nose"], subject="[tool.nose]")

    assert nose["top"], "`top` must be set so the graded surface stays bounded."
    assert nose["top"] != 0, (
        "`top = 0` would disable the ranking bound rather than widen the view."
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
