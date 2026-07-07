"""Unit tests for :func:`lading.commands.bump._update_dependency_sections`."""

from __future__ import annotations

from tomlkit import parse as parse_toml

from lading.commands import bump


def test_update_dependency_sections_with_workspace_flag() -> None:
    """The include_workspace_sections flag updates [workspace.dependencies]."""
    document = parse_toml(
        '[dependencies]\nalpha = "0.1.0"\n\n'
        '[workspace.dependencies]\nalpha = "^0.1.0"\n'
    )
    changed = bump._update_dependency_sections(
        document,
        {"dependencies": ("alpha",)},
        "1.0.0",
        include_workspace_sections=True,
    )
    assert changed is True
    assert document["dependencies"]["alpha"].value == "1.0.0"
    assert document["workspace"]["dependencies"]["alpha"].value == "^1.0.0"


def test_update_dependency_sections_without_workspace_flag() -> None:
    """Without the flag, [workspace.dependencies] is not updated."""
    document = parse_toml(
        '[dependencies]\nalpha = "0.1.0"\n\n'
        '[workspace.dependencies]\nalpha = "^0.1.0"\n'
    )
    changed = bump._update_dependency_sections(
        document,
        {"dependencies": ("alpha",)},
        "1.0.0",
        include_workspace_sections=False,
    )
    assert changed is True
    assert document["dependencies"]["alpha"].value == "1.0.0"
    # workspace.dependencies should remain unchanged
    assert document["workspace"]["dependencies"]["alpha"].value == "^0.1.0"


def test_update_dependency_sections_workspace_only() -> None:
    """When only workspace sections exist, they are updated with the flag."""
    document = parse_toml('[workspace.dependencies]\nalpha = "0.1.0"\n')
    changed = bump._update_dependency_sections(
        document,
        {"dependencies": ("alpha",)},
        "2.0.0",
        include_workspace_sections=True,
    )
    assert changed is True
    assert document["workspace"]["dependencies"]["alpha"].value == "2.0.0"


def test_update_dependency_sections_workspace_dev_and_build() -> None:
    """Workspace dev-dependencies and build-dependencies are updated."""
    document = parse_toml(
        '[workspace.dev-dependencies]\nalpha = "~0.1.0"\n\n'
        '[workspace.build-dependencies]\nbeta = { version = "0.1.0" }\n'
    )
    changed = bump._update_dependency_sections(
        document,
        {"dev-dependencies": ("alpha",), "build-dependencies": ("beta",)},
        "3.0.0",
        include_workspace_sections=True,
    )
    assert changed is True
    assert document["workspace"]["dev-dependencies"]["alpha"].value == "~3.0.0"
    build_deps = document["workspace"]["build-dependencies"]
    assert build_deps["beta"]["version"].value == "3.0.0"
