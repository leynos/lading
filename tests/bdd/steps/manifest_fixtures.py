"""Manifest-related behavioural fixtures for CLI scenarios."""

from __future__ import annotations

import typing as typ

from pytest_bdd import given, parsers
from tomlkit import inline_table, table

from lading.testing import toml_utils

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from cmd_mox import CmdMox


def _update_manifest_version(
    manifest_path: Path,
    version: str,
    keys: tuple[str, ...],
) -> None:
    """Update the version at the nested ``keys`` path in ``manifest_path``."""
    document = toml_utils.load_manifest(manifest_path)
    target = document
    for key in keys[:-1]:
        try:
            target = target[key]
        except KeyError as exc:  # pragma: no cover - defensive guard
            path = "/".join(keys)
            message = f"Key path {path!r} missing from manifest {manifest_path}"
            raise AssertionError(message) from exc
    target[keys[-1]] = version
    manifest_path.write_text(document.as_string(), encoding="utf-8")


def _update_crate_manifests(crates_root: Path, version: str) -> None:
    """Update the version in all crate manifests under ``crates_root``."""
    if not crates_root.exists():
        message = f"Crates directory not found: {crates_root}"
        raise AssertionError(message)
    for child in crates_root.iterdir():
        if not child.is_dir():
            continue
        manifest_path = child / "Cargo.toml"
        _update_manifest_version(
            manifest_path,
            version,
            ("package", "version"),
        )


def _prepare_published_gpui_e2e_fixture(
    cmd_mox: CmdMox,
    monkeypatch: pytest.MonkeyPatch,
    workspace_directory: Path,
) -> bytes:
    """Build the prerelease workspace and return the fixture's original bytes."""
    from tests.helpers.workspace_helpers import install_cargo_stub

    from .metadata_fixtures import _mock_cargo_metadata, _write_workspace_manifest
    from .test_data_helpers import _build_package_metadata, _create_test_crate

    version = "0.6.0-beta4"
    crate_names = (
        "rstest-bdd",
        "rstest-bdd-harness",
        "rstest-bdd-harness-gpui",
        "rstest-bdd-macros",
    )
    install_cargo_stub(cmd_mox, monkeypatch)
    manifests = [
        _create_test_crate(workspace_directory, name, version) for name in crate_names
    ]
    _write_workspace_manifest(
        workspace_directory,
        [f"crates/{name}" for name in crate_names],
        version=version,
    )
    _mock_cargo_metadata(
        cmd_mox,
        workspace_directory,
        packages=[
            _build_package_metadata(name, manifest, version=version)
            for name, manifest in zip(crate_names, manifests, strict=True)
        ],
        member_ids=[f"{name}-id" for name in crate_names],
    )
    fixture = workspace_directory / "tests/fixtures/published-gpui-e2e/Cargo.toml"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.write_text(
        """[dependencies]
rstest-bdd = "0.6.0-beta4"
rstest-bdd-harness = "0.6.0-beta4"
rstest-bdd-harness-gpui = "0.6.0-beta4"
rstest-bdd-macros = "0.6.0-beta4"

[patch.crates-io]
rstest-bdd = { path = "../../../target/published-gpui-e2e/rstest-bdd-0.6.0-beta4" }
"""
        """rstest-bdd-harness = { path = "../../../target/published-gpui-e2e/"""
        """rstest-bdd-harness-0.6.0-beta4" }
""",
        encoding="utf-8",
    )
    config_path = workspace_directory / "lading.toml"
    config_text = config_path.read_text(encoding="utf-8")
    config_text += (
        "\n[[bump.manifest_rewrites]]\n"
        'paths = ["tests/fixtures/published-gpui-e2e/Cargo.toml"]\n'
        "\n[[bump.manifest_rewrites.string_values]]\n"
        'table = ["patch", "crates-io"]\n'
        'field = "path"\n'
        'template = "{crate}-{version}"\n'
    )
    config_path.write_text(config_text, encoding="utf-8")
    return fixture.read_bytes()


@given(parsers.parse('the workspace manifests record version "{version}"'))
def given_workspace_versions_match(
    workspace_directory: Path,
    version: str,
) -> None:
    """Ensure the workspace and member manifests record ``version``."""
    workspace_manifest = workspace_directory / "Cargo.toml"
    _update_manifest_version(
        workspace_manifest,
        version,
        ("workspace", "package", "version"),
    )
    crates_root = workspace_directory / "crates"
    _update_crate_manifests(crates_root, version)


@given(parsers.parse('the workspace file "{relative_path}" contains "{contents}"'))
def given_workspace_file_contents(
    workspace_directory: Path, relative_path: str, contents: str
) -> None:
    """Create or overwrite ``relative_path`` with ``contents`` inside the workspace."""
    target = workspace_directory / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(contents, encoding="utf-8")


@given(parsers.parse('the workspace manifest patches crates "{crate_names}"'))
def given_workspace_manifest_patch_entries(
    workspace_directory: Path,
    crate_names: str,
) -> None:
    """Ensure ``[patch.crates-io]`` defines entries for ``crate_names``."""
    manifest_path = workspace_directory / "Cargo.toml"
    names = [name.strip() for name in crate_names.split(",") if name.strip()]
    document = toml_utils.load_manifest(manifest_path)
    patch_table = document.get("patch")
    if patch_table is None:
        patch_table = table()
        document["patch"] = patch_table
    crates_io = patch_table.get("crates-io")
    if crates_io is None:
        crates_io = table()
        patch_table["crates-io"] = crates_io
    for name in names:
        entry = inline_table()
        entry.update({"path": f"../{name}"})
        crates_io[name] = entry
    manifest_path.write_text(document.as_string(), encoding="utf-8")
