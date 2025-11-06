"""Configuration-related behavioural fixtures for CLI scenarios."""

from __future__ import annotations

import typing as typ

from pytest_bdd import given, parsers
from tomlkit import array, table
from tomlkit import document as make_document
from tomlkit import parse as parse_toml

from lading import config as config_module

if typ.TYPE_CHECKING:
    from pathlib import Path


def _add_exclude_to_config(
    workspace_directory: Path,
    table_name: str,
    crate_name: str,
) -> None:
    """Ensure ``crate_name`` appears in the ``{table_name}.exclude`` configuration."""
    config_path = workspace_directory / config_module.CONFIG_FILENAME
    if config_path.exists():
        doc = parse_toml(config_path.read_text(encoding="utf-8"))
    else:
        doc = make_document()
    table_section = doc.get(table_name)
    if table_section is None:
        table_section = table()
        doc[table_name] = table_section
    exclude = table_section.get("exclude")
    if exclude is None:
        exclude = array()
        table_section["exclude"] = exclude
    if crate_name not in exclude:
        exclude.append(crate_name)
    config_path.write_text(doc.as_string(), encoding="utf-8")


def _load_or_create_toml_document(config_path: Path) -> typ.Any:
    """Parse ``config_path`` if it exists, otherwise return a new document."""
    if config_path.exists():
        return parse_toml(config_path.read_text(encoding="utf-8"))
    return make_document()


def _ensure_table_exists(doc: typ.Any, table_name: str) -> typ.Any:
    """Fetch or create ``table_name`` within ``doc``."""
    table_section = doc.get(table_name)
    if table_section is None:
        table_section = table()
        doc[table_name] = table_section
    return table_section


def _ensure_array_field_exists(parent_table: typ.Any, field_name: str) -> typ.Any:
    """Fetch or create an array field inside ``parent_table``."""
    raw_field = parent_table.get(field_name)
    if raw_field is None:
        field_array = array()
        parent_table[field_name] = field_array
        return field_array
    if hasattr(raw_field, "append"):
        return raw_field
    message = f"{field_name} must be an array"
    raise AssertionError(message)  # pragma: no cover - defensive guard


def _append_if_absent(target_array: typ.Any, value: str) -> None:
    """Append ``value`` to ``target_array`` if it is not already present."""
    if value not in target_array:
        target_array.append(value)


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
    _add_exclude_to_config(workspace_directory, "bump", crate_name)


@given(parsers.parse('publish.exclude contains "{crate_name}"'))
def given_publish_exclude_contains(
    workspace_directory: Path,
    crate_name: str,
) -> None:
    """Ensure ``crate_name`` appears in the ``publish.exclude`` configuration."""
    _add_exclude_to_config(workspace_directory, "publish", crate_name)


@given(parsers.parse('preflight.test_exclude contains "{crate_name}"'))
def given_preflight_test_exclude_contains(
    workspace_directory: Path,
    crate_name: str,
) -> None:
    """Ensure ``crate_name`` appears in ``preflight.test_exclude``."""
    config_path = workspace_directory / config_module.CONFIG_FILENAME
    document = _load_or_create_toml_document(config_path)
    preflight_table = _ensure_table_exists(document, "preflight")
    excludes_array = _ensure_array_field_exists(preflight_table, "test_exclude")
    _append_if_absent(excludes_array, crate_name)
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
