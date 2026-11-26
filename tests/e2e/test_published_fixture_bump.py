"""Exercise non-member fixture rewrites with real Cargo lockfile updates."""

import json
import shutil
import subprocess
import typing as typ
from pathlib import Path

import pytest
from tomlkit import dumps
from tomlkit import parse as parse_toml

from lading import config as config_module
from lading.commands import bump, bump_toml_files
from lading.config import ManifestRewriteConfig, StringValueRewriteConfig
from tests.e2e.helpers.git_helpers import git_add_all, git_init
from tests.helpers.workspace_builders import (
    _build_workspace_with_internal_deps,
    _CrateSpec,
)

if typ.TYPE_CHECKING:
    from lading.workspace import WorkspaceGraph

_CARGO_EXECUTABLE = shutil.which("cargo")

pytestmark = [
    pytest.mark.timeout(120),
    pytest.mark.usefixtures("reset_process_metrics"),
    pytest.mark.skipif(
        _CARGO_EXECUTABLE is None,
        reason="Cargo is required for this end-to-end check",
    ),
]

_CRATE_NAMES = (
    "rstest-bdd",
    "rstest-bdd-harness",
    "rstest-bdd-harness-gpui",
    "rstest-bdd-macros",
    "rstest-bdd-patterns",
    "rstest-bdd-policy",
)
_DIRECT_DEPENDENCIES = _CRATE_NAMES[:4]
_PRERELEASE = "0.6.0-beta4"
_TARGET = "0.6.0"


def _write_staged_package(
    workspace_root: Path,
    crate_name: str,
    version: str,
) -> None:
    """Write one local staged package for the standalone publication fixture."""
    package_root = (
        workspace_root / "target/published-gpui-e2e" / f"{crate_name}-{version}"
    )
    source_directory = package_root / "src"
    source_directory.mkdir(parents=True, exist_ok=True)
    manifest = (
        f'[package]\nname = "{crate_name}"\nversion = "{version}"\nedition = "2021"\n'
    )
    if crate_name == "rstest-bdd-harness":
        manifest += (
            "\n[dependencies]\n"
            'rstest-bdd-patterns = ">=0.6.0-beta4"\n'
            'rstest-bdd-policy = ">=0.6.0-beta4"\n'
        )
    (package_root / "Cargo.toml").write_text(manifest, encoding="utf-8")
    (source_directory / "lib.rs").write_text(
        "// lockfile-only package\n", encoding="utf-8"
    )


def _write_fixture(workspace_root: Path) -> Path:
    """Write the standalone manifest with all six staged package paths."""
    fixture = workspace_root / "tests/fixtures/published-gpui-e2e/Cargo.toml"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    manifest = (
        "[package]\n"
        'name = "published-gpui-e2e-fixture"\n'
        'version = "0.0.0"\n'
        'edition = "2021"\n'
        "publish = false\n\n"
        "[dependencies]\n"
        + "".join(
            f'{crate_name} = "{_PRERELEASE}"\n' for crate_name in _DIRECT_DEPENDENCIES
        )
        + '\n[workspace]\nresolver = "2"\n\n[patch.crates-io]\n'
        + "".join(
            f'{crate_name} = {{ path = "../../../target/published-gpui-e2e/'
            f'{crate_name}-{_PRERELEASE}" }}\n'
            for crate_name in _CRATE_NAMES
        )
    )
    fixture.write_text(manifest, encoding="utf-8")
    (fixture.parent / "src").mkdir()
    (fixture.parent / "src/lib.rs").write_text(
        "// lockfile-only fixture package\n", encoding="utf-8"
    )
    return fixture


def _run_cargo(*arguments: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run Cargo with fixed arguments and return its captured result."""
    assert _CARGO_EXECUTABLE is not None, "the test's skip marker requires Cargo"
    # ruff: ignore[subprocess-without-shell-equals-true] - fixed test argv
    return subprocess.run(
        (_CARGO_EXECUTABLE, *arguments),
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _package_versions(lockfile: Path) -> dict[str, tuple[str, ...]]:
    """Read package versions from a generated Cargo lockfile."""
    import tomllib

    document = tomllib.loads(lockfile.read_text(encoding="utf-8"))
    return {
        crate_name: tuple(
            sorted(
                package["version"]
                for package in document["package"]
                if package["name"] == crate_name
            )
        )
        for crate_name in _CRATE_NAMES
    }


def _write_initial_lockfiles(workspace_root: Path, fixture: Path) -> None:
    """Generate and track the root and standalone fixture lockfiles."""
    _run_cargo(
        "generate-lockfile",
        "--offline",
        "--manifest-path",
        "Cargo.toml",
        cwd=workspace_root,
    )
    _run_cargo(
        "generate-lockfile",
        "--offline",
        "--manifest-path",
        str(fixture),
        cwd=workspace_root,
    )
    git_init(workspace_root)
    git_add_all(workspace_root)


def _prepare_tracked_fixture_workspace(
    workspace_root: Path,
) -> tuple[WorkspaceGraph, Path]:
    """Build a prerelease workspace and track its standalone fixture lockfile."""
    workspace, member_manifests = _build_workspace_with_internal_deps(
        workspace_root,
        specs=tuple(
            _CrateSpec(name=crate_name, version=_PRERELEASE)
            for crate_name in _CRATE_NAMES
        ),
    )
    root_manifest = workspace_root / "Cargo.toml"
    root_document = parse_toml(root_manifest.read_text(encoding="utf-8"))
    root_document["workspace"]["package"]["version"] = _PRERELEASE
    root_manifest.write_text(dumps(root_document), encoding="utf-8")
    for manifest_path in member_manifests.values():
        source_directory = manifest_path.parent / "src"
        source_directory.mkdir()
        (source_directory / "lib.rs").write_text(
            "// lockfile-only workspace package\n", encoding="utf-8"
        )
    for version in (_PRERELEASE, _TARGET):
        for crate_name in _CRATE_NAMES:
            _write_staged_package(workspace_root, crate_name, version)
    fixture = _write_fixture(workspace_root)
    (workspace_root / ".gitignore").write_text("target/\n", encoding="utf-8")
    _write_initial_lockfiles(workspace_root, fixture)
    return workspace, fixture


def _fixture_rewrite_configuration() -> config_module.LadingConfig:
    """Configure the published GPUI fixture's dependency and patch rewrites."""
    rewrite = ManifestRewriteConfig(
        paths=("tests/fixtures/published-gpui-e2e/Cargo.toml",),
        string_values=(
            StringValueRewriteConfig(table=("patch", "crates-io"), field="path"),
        ),
    )
    return config_module.LadingConfig(
        bump=config_module.BumpConfig(manifest_rewrites=(rewrite,))
    )


def _assert_rewritten_fixture(fixture: Path, result: str) -> None:
    """Check rewritten requirements, six paths, and bump output."""
    document = parse_toml(fixture.read_text(encoding="utf-8"))
    assert {
        crate_name: str(document["dependencies"][crate_name])
        for crate_name in _DIRECT_DEPENDENCIES
    } == dict.fromkeys(_DIRECT_DEPENDENCIES, _TARGET), (
        "all direct prerelease requirements must use the release version"
    )
    assert {
        crate_name: str(document["patch"]["crates-io"][crate_name]["path"])
        for crate_name in _CRATE_NAMES
    } == {
        crate_name: f"../../../target/published-gpui-e2e/{crate_name}-{_TARGET}"
        for crate_name in _CRATE_NAMES
    }, "all six staged package paths must use the release version"
    assert "tests/fixtures/published-gpui-e2e/Cargo.toml" in result, (
        "the rewritten fixture must appear in the bump result"
    )


def _assert_fixture_lockfile_resolves_target(
    workspace_root: Path,
    fixture: Path,
) -> None:
    """Check that Cargo's tracked fixture lockfile resolves all release packages."""
    lockfile_versions = _package_versions(fixture.with_name("Cargo.lock"))
    assert lockfile_versions == dict.fromkeys(_CRATE_NAMES, (_TARGET,)), (
        "tracked fixture lockfile regeneration must select all six release packages"
    )
    metadata = json.loads(
        _run_cargo(
            "metadata",
            "--locked",
            "--format-version",
            "1",
            "--manifest-path",
            str(fixture),
            cwd=workspace_root,
        ).stdout
    )
    resolved_versions = {
        crate_name: tuple(
            sorted(
                package["version"]
                for package in metadata["packages"]
                if package["name"] == crate_name
            )
        )
        for crate_name in _CRATE_NAMES
    }
    assert resolved_versions == dict.fromkeys(_CRATE_NAMES, (_TARGET,)), (
        "Cargo metadata --locked must resolve every staged package at the target"
    )


def test_bump_rewrites_and_regenerates_tracked_published_fixture_lockfile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cargo accepts the full rewritten fixture and its regenerated lockfile."""
    if not bump_toml_files._supports_bounded_directory_operations():
        pytest.skip("workspace-bounded atomic writes are unavailable")
    monkeypatch.setenv("CARGO_NET_OFFLINE", "true")
    cargo_home = tmp_path / "cargo-home"
    cargo_home.mkdir()
    # Keep registry contents out of this offline test's Cargo search path.
    assert not (cargo_home / "registry").exists(), (
        "the isolated Cargo home must not expose the shared registry cache"
    )
    monkeypatch.setenv("CARGO_HOME", str(cargo_home))
    workspace, fixture = _prepare_tracked_fixture_workspace(tmp_path)

    result = bump.run(
        tmp_path,
        _TARGET,
        options=bump.BumpOptions(
            configuration=_fixture_rewrite_configuration(),
            workspace=workspace,
        ),
    )

    _assert_rewritten_fixture(fixture, result)
    _assert_fixture_lockfile_resolves_target(tmp_path, fixture)
