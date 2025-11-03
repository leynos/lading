"""Tests for ``lading.config``."""

from __future__ import annotations

import textwrap
import typing as typ

import pytest

from lading import config as config_module

if typ.TYPE_CHECKING:
    from pathlib import Path


def _write_config(tmp_path: Path, body: str) -> Path:
    config_path = tmp_path / config_module.CONFIG_FILENAME
    config_path.write_text(textwrap.dedent(body).lstrip())
    return config_path


def test_load_configuration_parses_values(tmp_path: Path) -> None:
    """Load a representative configuration document."""
    _write_config(
        tmp_path,
        """
        [bump]
        exclude = ["internal"]

        [bump.documentation]
        globs = ["README.md", "docs/**/*.md"]

        [publish]
        exclude = ["examples"]
        order = ["core"]
        strip_patches = "all"

        [preflight]
        test_exclude = ["cucumber"]
        unit_tests_only = true
        """,
    )

    configuration = config_module.load_configuration(tmp_path)

    assert configuration.bump.exclude == ("internal",)
    assert configuration.bump.documentation.globs == (
        "README.md",
        "docs/**/*.md",
    )
    assert configuration.publish.exclude == ("examples",)
    assert configuration.publish.order == ("core",)
    assert configuration.publish.strip_patches == "all"
    assert configuration.preflight.test_exclude == ("cucumber",)
    assert configuration.preflight.unit_tests_only is True


@pytest.mark.parametrize(
    "config_body",
    [
        pytest.param(
            """
            [publish]
            strip_patches = true
            """,
            id="invalid_strip_patches_bool",
        ),
        pytest.param(
            """
            [publish]
            strip_patches = "unexpected"
            """,
            id="invalid_strip_patches_string",
        ),
        pytest.param(
            """
            [bump]
            exclude = []

            [bump.documentation]
            unknown = "value"
            """,
            id="documentation_unknown",
        ),
        pytest.param(
            """
            [publish]
            unexpected = "value"
            """,
            id="unknown_keys",
        ),
        pytest.param(
            """
            [preflight]
            unknown = true
            """,
            id="preflight_unknown_key",
        ),
        pytest.param(
            """
            [preflight]
            test_exclude = ["alpha", 1]
            """,
            id="preflight_invalid_type",
        ),
        pytest.param(
            """
            [preflight]
            unit_tests_only = "sometimes"
            """,
            id="preflight_invalid_boolean",
        ),
        pytest.param(
            """
            [unknown]
            value = 1
            """,
            id="unknown_sections",
        ),
    ],
)
def test_load_configuration_rejects_invalid_values(
    tmp_path: Path, config_body: str
) -> None:
    """Reject invalid configuration values and structures."""
    _write_config(tmp_path, config_body)

    with pytest.raises(config_module.ConfigurationError):
        config_module.load_configuration(tmp_path)


def test_load_configuration_applies_defaults(tmp_path: Path) -> None:
    """Missing tables fall back to default values."""
    _write_config(tmp_path, "# empty file still constitutes valid TOML")

    configuration = config_module.load_configuration(tmp_path)

    assert configuration.publish.strip_patches == "per-crate"
    assert configuration.bump.documentation.globs == ()
    assert configuration.preflight.unit_tests_only is False


def test_load_configuration_requires_file(tmp_path: Path) -> None:
    """Raise a descriptive error when ``lading.toml`` is absent."""
    with pytest.raises(config_module.MissingConfigurationError):
        config_module.load_configuration(tmp_path)


def test_use_configuration_sets_context(tmp_path: Path) -> None:
    """The configuration context manager exposes the active configuration."""
    _write_config(tmp_path, "")
    configuration = config_module.load_configuration(tmp_path)

    with pytest.raises(config_module.ConfigurationNotLoadedError):
        config_module.current_configuration()

    with config_module.use_configuration(configuration):
        assert config_module.current_configuration() is configuration

    with pytest.raises(config_module.ConfigurationNotLoadedError):
        config_module.current_configuration()


def test_nested_use_configuration_contexts(tmp_path: Path) -> None:
    """Nested configuration contexts restore the previous configuration."""
    _write_config(tmp_path, "")
    config_a = config_module.load_configuration(tmp_path)

    alternate_root = tmp_path.parent / f"{tmp_path.name}_alt"
    alternate_root.mkdir()
    _write_config(alternate_root, "")
    config_b = config_module.load_configuration(alternate_root)

    with config_module.use_configuration(config_a):
        assert config_module.current_configuration() is config_a
        with config_module.use_configuration(config_b):
            assert config_module.current_configuration() is config_b
        assert config_module.current_configuration() is config_a

    with pytest.raises(config_module.ConfigurationNotLoadedError):
        config_module.current_configuration()
