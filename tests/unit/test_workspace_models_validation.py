"""Validation-focused tests for :mod:`lading.workspace.models`."""

from __future__ import annotations

import typing as typ

import pytest

from lading.workspace import models

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers only
    from pathlib import Path


def test_is_ordering_dependency_skips_unknown_crates() -> None:
    """Dependencies outside the workspace should not influence ordering."""
    dependency = models.WorkspaceDependency(
        package_id="pkg",
        name="external",
        manifest_name="external",
    )

    assert models._is_ordering_dependency(dependency, {}) is False


def test_build_workspace_graph_requires_workspace_root() -> None:
    """Missing workspace_root entries should raise an error."""
    with pytest.raises(models.WorkspaceModelError, match="workspace_root"):
        models.build_workspace_graph({"packages": [], "workspace_members": []})


def test_index_workspace_packages_skips_non_members() -> None:
    """Only workspace member packages should be indexed."""
    packages = [{"id": "member"}, {"id": "external"}]

    index = models._index_workspace_packages(packages, ["member"])

    assert set(index) == {"member"}


def test_build_dependencies_handles_missing_entries() -> None:
    """None dependencies should be treated as empty."""
    package = {"id": "crate", "dependencies": None}

    dependencies = models._build_dependencies(package, {}, set())

    assert dependencies == ()


@pytest.mark.parametrize(
    ("callable_obj", "args"),
    [
        pytest.param(
            models._validate_dependency_mapping,
            ("not-a-mapping",),
            id="mapping_not_dict",
        ),
        pytest.param(
            models._validate_dependency_kind,
            ({"kind": 123},),
            id="kind_not_string",
        ),
        pytest.param(
            models._validate_dependency_kind,
            ({"kind": "unknown"},),
            id="kind_unsupported",
        ),
    ],
)
def test_dependency_validation_errors(
    callable_obj: typ.Callable[..., object], args: tuple[object, ...]
) -> None:
    """Invalid dependency shapes should raise WorkspaceModelError."""
    with pytest.raises(models.WorkspaceModelError):
        callable_obj(*args)


def test_lookup_workspace_target_handles_missing_entries() -> None:
    """Targets outside the workspace should return None."""
    result = models._lookup_workspace_target({}, {}, set())

    assert result is None


def test_path_normalisation_rejects_invalid_types() -> None:
    """Non-path types should be rejected for manifest and root paths."""
    with pytest.raises(models.WorkspaceModelError):
        models._normalise_workspace_root(123)
    with pytest.raises(models.WorkspaceModelError):
        models._normalise_manifest_path(123, "field")


def test_expect_sequence_validation() -> None:
    """Sequence validation should honour allow_none and reject scalars."""
    assert models._expect_sequence(None, "field", allow_none=True) is None
    with pytest.raises(models.WorkspaceModelError):
        models._expect_sequence(None, "field")
    with pytest.raises(models.WorkspaceModelError):
        models._expect_sequence("oops", "field")


def test_expect_string_and_non_empty_sequence_checks() -> None:
    """String and sequence coercion should reject invalid inputs."""
    with pytest.raises(models.WorkspaceModelError):
        models._expect_string(123, "field")
    assert models._is_non_empty_sequence([]) is False
    assert models._is_non_empty_sequence("abc") is False
    assert models._is_non_empty_sequence(["a"]) is True


def test_coerce_publish_setting_allows_sequences_and_bools() -> None:
    """Publish setting coercion should support bools, lists, and None."""
    assert models._coerce_publish_setting(None, "crate") is True
    assert models._coerce_publish_setting(value=False, package_id="crate") is False
    assert models._coerce_publish_setting(["crates-io"], "crate") is True
    with pytest.raises(models.WorkspaceModelError):
        models._coerce_publish_setting("invalid", "crate")


def test_extract_readme_workspace_flag_handles_non_mappings(tmp_path: Path) -> None:
    """Non-mapping package tables should return False."""
    assert models._extract_readme_workspace_flag("invalid") is False
    assert models._extract_readme_workspace_flag({"readme": "README.md"}) is False


def test_manifest_uses_workspace_readme_detects_flag(tmp_path: Path) -> None:
    """Manifest helper should detect readme.workspace usage."""
    manifest_path = tmp_path / "Cargo.toml"
    manifest_path.write_text("[package]\nname = 'demo'\nreadme.workspace = true\n")
    assert models._manifest_uses_workspace_readme(manifest_path) is True


def test_manifest_uses_workspace_readme_reports_parse_errors(tmp_path: Path) -> None:
    """Malformed manifests should raise WorkspaceModelError."""
    manifest_path = tmp_path / "Cargo.toml"
    manifest_path.write_text("[package\n", encoding="utf-8")

    with pytest.raises(models.WorkspaceModelError):
        models._manifest_uses_workspace_readme(manifest_path)
