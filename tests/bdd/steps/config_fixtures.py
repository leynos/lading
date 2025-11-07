"""Configuration-related behavioural fixtures for CLI scenarios."""

from __future__ import annotations

import typing as typ

from pytest_bdd import given, parsers
from tomlkit import array, table
from tomlkit import document as make_document
from tomlkit import parse as parse_toml

from lading import config as config_module
from tests.bdd import toml_utils

if typ.TYPE_CHECKING:
    from pathlib import Path


def _add_exclude_to_config(
    workspace_directory: Path,
    table_name: str,
    field_name: str,
    crate_name: str,
) -> None:
    """Ensure ``crate_name`` appears in ``{table_name}.{field_name}``."""
    config_path = workspace_directory / config_module.CONFIG_FILENAME
    document = toml_utils.load_or_create_document(config_path)
    table_section = toml_utils.ensure_table(document, table_name)
    exclude = toml_utils.ensure_array_field(table_section, field_name)
    toml_utils.append_if_absent(exclude, crate_name)
    config_path.write_text(document.as_string(), encoding="utf-8")


@given("a workspace directory with configuration", target_fixture="workspace_directory")
def given_workspace_directory(tmp_path: Path) -> Path:
    """Provide a temporary workspace root for CLI exercises."""
    config_path = tmp_path / config_module.CONFIG_FILENAME
    config_path.write_text(
        '[bump]\n\n[publish]\nstrip_patches = "all"\n', encoding="utf-8"
    )
    return tmp_path


@given(
    "a workspace directory without configuration",
    target_fixture="workspace_directory",
)
def given_workspace_without_configuration(tmp_path: Path) -> Path:
    """Provide a workspace root without a configuration file."""
    return tmp_path


@given(parsers.parse('bump.documentation.globs contains "{pattern}"'))
def given_documentation_glob(workspace_directory: Path, pattern: str) -> None:
    """Append ``pattern`` to the documentation glob list in ``lading.toml``."""
    config_path = workspace_directory / config_module.CONFIG_FILENAME
    document = parse_toml(config_path.read_text(encoding="utf-8"))
    bump_table = document.get("bump")
    if bump_table is None:
        bump_table = table()
        document["bump"] = bump_table
    documentation_table = bump_table.get("documentation")
    if documentation_table is None:
        documentation_table = table()
        bump_table["documentation"] = documentation_table
    globs_value = documentation_table.get("globs")
    if globs_value is None:
        globs_array = array()
        documentation_table["globs"] = globs_array
    elif hasattr(globs_value, "append"):
        globs_array = globs_value
    else:  # pragma: no cover - defensive guard for unexpected config edits
        message = "bump.documentation.globs must be an array"
        raise AssertionError(message)
    globs_array.append(pattern)
    config_path.write_text(document.as_string(), encoding="utf-8")


@given(parsers.parse('bump.exclude contains "{crate_name}"'))
def given_bump_exclude_contains(
    workspace_directory: Path,
    crate_name: str,
) -> None:
    """Ensure ``crate_name`` appears in the ``bump.exclude`` configuration."""
    _add_exclude_to_config(workspace_directory, "bump", "exclude", crate_name)


@given(parsers.parse('publish.exclude contains "{crate_name}"'))
def given_publish_exclude_contains(
    workspace_directory: Path,
    crate_name: str,
) -> None:
    """Ensure ``crate_name`` appears in the ``publish.exclude`` configuration."""
    _add_exclude_to_config(workspace_directory, "publish", "exclude", crate_name)


@given(parsers.parse('preflight.test_exclude contains "{crate_name}"'))
def given_preflight_test_exclude_contains(
    workspace_directory: Path,
    crate_name: str,
) -> None:
    """Ensure ``crate_name`` appears in ``preflight.test_exclude``."""
    _add_exclude_to_config(workspace_directory, "preflight", "test_exclude", crate_name)


@given("preflight.test_exclude contains blank entries")
def given_preflight_test_exclude_blank_entries(workspace_directory: Path) -> None:
    """Populate ``preflight.test_exclude`` with blank values for sanitisation tests."""
    config_path = workspace_directory / config_module.CONFIG_FILENAME
    document = toml_utils.load_or_create_document(config_path)
    preflight_table = toml_utils.ensure_table(document, "preflight")
    blank_entries = array()
    for value in ("", "   ", "\t", "\n"):
        blank_entries.append(value)
    preflight_table["test_exclude"] = blank_entries
    config_path.write_text(document.as_string(), encoding="utf-8")


@given("preflight.unit_tests_only is true")
def given_preflight_unit_tests_only_true(workspace_directory: Path) -> None:
    """Enable unit-tests-only mode for publish pre-flight checks."""
    config_path = workspace_directory / config_module.CONFIG_FILENAME
    if config_path.exists():
        doc = parse_toml(config_path.read_text(encoding="utf-8"))
    else:
        doc = make_document()
    preflight_table = doc.get("preflight")
    if preflight_table is None:
        preflight_table = table()
        doc["preflight"] = preflight_table
    preflight_table["unit_tests_only"] = True
    config_path.write_text(doc.as_string(), encoding="utf-8")


@given(parsers.parse('publish.order is "{order}"'))
def given_publish_order_is(workspace_directory: Path, order: str) -> None:
    """Set the publish order configuration to ``order``."""
    names = [name.strip() for name in order.split(",") if name.strip()]
    config_path = workspace_directory / config_module.CONFIG_FILENAME
    if config_path.exists():
        doc = parse_toml(config_path.read_text(encoding="utf-8"))
    else:
        doc = make_document()
    publish_table = doc.get("publish")
    if publish_table is None:
        publish_table = table()
        doc["publish"] = publish_table
    order_array = array()
    for name in names:
        order_array.append(name)
    publish_table["order"] = order_array
    config_path.write_text(doc.as_string(), encoding="utf-8")


@given(
    parsers.parse(
        'the workspace README contains a TOML dependency snippet for "{crate_name}"'
    )
)
def given_workspace_readme_snippet(workspace_directory: Path, crate_name: str) -> None:
    """Write a README with a TOML fence referencing ``crate_name``."""
    import textwrap

    readme_path = workspace_directory / "README.md"
    content = textwrap.dedent(
        f"""
        # Usage

        ```toml
        [dependencies]
        {crate_name} = "0.1.0"
        ```
        """
    ).lstrip()
    readme_path.write_text(content, encoding="utf-8")
