"""Tests for ``lading.config``."""

from __future__ import annotations

import re
import textwrap
import typing as typ

import pytest

from lading import config as config_module

if typ.TYPE_CHECKING:
    from pathlib import Path


def _write_config(tmp_path: Path, body: str) -> Path:
    """Write a lading.toml with *body* into *tmp_path*."""
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
        lockfile_manifests = ["crates/nested/Cargo.toml"]
        rebuild_lockfiles = false

        [bump.documentation]
        globs = ["README.md", "docs/**/*.md"]

        [publish]
        exclude = ["examples"]
        order = ["core"]
        strip_patches = "all"

        [preflight]
        skip = true
        test_exclude = ["cucumber"]
        unit_tests_only = true
        """,
    )

    configuration = config_module.load_configuration(tmp_path)

    assert configuration.bump.exclude == ("internal",)
    assert configuration.bump.lockfile_manifests == ("crates/nested/Cargo.toml",)
    assert configuration.bump.rebuild_lockfiles is False
    assert configuration.bump.documentation.globs == (
        "README.md",
        "docs/**/*.md",
    )
    assert configuration.publish.exclude == ("examples",)
    assert configuration.publish.order == ("core",)
    assert configuration.publish.strip_patches == "all"
    assert configuration.preflight.test_exclude == ("cucumber",)
    assert configuration.preflight.unit_tests_only is True
    assert configuration.preflight.skip is True, (
        "an explicit [preflight] skip = true should be parsed"
    )


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
            lockfile_manifests = [1]
            """,
            id="bump_invalid_lockfile_manifests",
        ),
        pytest.param(
            """
            [bump]
            rebuild_lockfiles = "sometimes"
            """,
            id="bump_invalid_rebuild_lockfiles",
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
            skip = "sometimes"
            """,
            id="preflight_skip_invalid_boolean",
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
    assert configuration.bump.lockfile_manifests == ()
    assert configuration.bump.rebuild_lockfiles is True
    assert configuration.bump.documentation.globs == ()
    assert configuration.preflight.unit_tests_only is False


def test_load_configuration_defaults_without_file(tmp_path: Path) -> None:
    """Missing configuration files fall back to the default configuration."""
    configuration = config_module.load_configuration(tmp_path)

    assert configuration == config_module.LadingConfig()


def test_preflight_config_from_mapping_parses_fields() -> None:
    """PreflightConfig.from_mapping converts values into tuples and booleans."""
    mapping = {"test_exclude": ["alpha", "beta"], "unit_tests_only": True}

    configuration = config_module.PreflightConfig.from_mapping(mapping)

    assert configuration.test_exclude == ("alpha", "beta")
    assert configuration.unit_tests_only is True


def test_preflight_config_from_mapping_defaults() -> None:
    """Missing preflight table falls back to the default configuration."""
    configuration = config_module.PreflightConfig.from_mapping(None)

    assert configuration.test_exclude == ()
    assert configuration.unit_tests_only is False
    assert configuration.skip is False


def test_bump_config_from_mapping_parses_lockfile_fields() -> None:
    """BumpConfig.from_mapping normalizes lockfile settings."""
    mapping = {
        "lockfile_manifests": ["crates/nested/Cargo.toml"],
        "rebuild_lockfiles": True,
    }

    configuration = config_module.BumpConfig.from_mapping(mapping)

    assert configuration.lockfile_manifests == ("crates/nested/Cargo.toml",)
    assert configuration.rebuild_lockfiles is True


def test_bump_config_from_mapping_defaults_lockfile_fields() -> None:
    """Missing bump table falls back to lockfile rebuild defaults."""
    configuration = config_module.BumpConfig.from_mapping(None)

    assert configuration.lockfile_manifests == ()
    assert configuration.rebuild_lockfiles is True
    assert configuration.manifest_rewrites == ()


def test_bump_config_from_mapping_parses_manifest_rewrites() -> None:
    """Manifest rewrite configuration defaults and selectors are immutable."""
    configuration = config_module.BumpConfig.from_mapping({
        "manifest_rewrites": [
            {
                "paths": ["tests/fixtures/published-gpui-e2e/Cargo.toml"],
                "string_values": [
                    {
                        "table": ["patch", "crates-io"],
                        "field": "path",
                    }
                ],
            }
        ],
    })

    assert configuration.manifest_rewrites == (
        config_module.ManifestRewriteConfig(
            paths=("tests/fixtures/published-gpui-e2e/Cargo.toml",),
            dependencies=True,
            string_values=(
                config_module.StringValueRewriteConfig(
                    table=("patch", "crates-io"),
                    field="path",
                    template="{crate}-{version}",
                ),
            ),
        ),
    )


@pytest.mark.parametrize(
    ("mapping", "error_message"),
    [
        pytest.param(
            {"manifest_rewrites": "tests/fixture/Cargo.toml"},
            "bump.manifest_rewrites must be a sequence; received str.",
            id="bare_string",
        ),
        pytest.param(
            {"manifest_rewrites": {"paths": ["tests/fixture/Cargo.toml"]}},
            "bump.manifest_rewrites must be a sequence; received dict.",
            id="bare_table",
        ),
        pytest.param(
            {"manifest_rewrites": [{}]},
            "bump.manifest_rewrites[0].paths must be a sequence; received NoneType.",
            id="missing_paths",
        ),
        pytest.param(
            {"manifest_rewrites": [{"paths": []}]},
            "bump.manifest_rewrites[0].paths must contain at least one path.",
            id="empty_paths",
        ),
        pytest.param(
            {"manifest_rewrites": [{"paths": ["  "]}]},
            "bump.manifest_rewrites[0].paths[0] must not be blank.",
            id="blank_path",
        ),
        pytest.param(
            {"manifest_rewrites": [{"paths": ["/workspace/Cargo.toml"]}]},
            "bump.manifest_rewrites[0].paths[0] must be relative to the workspace.",
            id="absolute_path",
        ),
        pytest.param(
            {"manifest_rewrites": [{"paths": ["C:\\tmp\\Cargo.toml"]}]},
            "bump.manifest_rewrites[0].paths[0] must be relative to the workspace.",
            id="windows_absolute_path",
        ),
        pytest.param(
            {"manifest_rewrites": [{"paths": ["tests/Cargo.toml"], "extra": True}]},
            "Unknown bump.manifest_rewrites[0] option(s): extra.",
            id="unknown_group_key",
        ),
        pytest.param(
            {
                "manifest_rewrites": [
                    {"paths": ["tests/Cargo.toml"], "dependencies": "yes"}
                ]
            },
            "bump.manifest_rewrites[0].dependencies must be a boolean; received str.",
            id="invalid_dependencies_boolean",
        ),
        pytest.param(
            {
                "manifest_rewrites": [
                    {"paths": ["tests/Cargo.toml"], "dependencies": False}
                ]
            },
            "bump.manifest_rewrites[0] must enable dependencies "
            "or define string_values.",
            id="dependencies_disabled_without_string_rules",
        ),
        pytest.param(
            {
                "manifest_rewrites": [
                    {
                        "paths": ["tests/Cargo.toml"],
                        "string_values": {"table": ["patch"]},
                    }
                ],
            },
            "bump.manifest_rewrites[0].string_values must be a sequence; "
            "received dict.",
            id="string_values_bare_table",
        ),
        pytest.param(
            {
                "manifest_rewrites": [
                    {
                        "paths": ["tests/Cargo.toml"],
                        "string_values": [{"table": "patch"}],
                    }
                ],
            },
            "bump.manifest_rewrites[0].string_values[0].table must be a sequence; "
            "received str.",
            id="table_bare_string",
        ),
        pytest.param(
            {
                "manifest_rewrites": [
                    {
                        "paths": ["tests/Cargo.toml"],
                        "dependencies": False,
                        "string_values": [{"table": []}],
                    }
                ],
            },
            "bump.manifest_rewrites[0].string_values[0].table must contain "
            "at least one segment.",
            id="empty_table_selector",
        ),
        pytest.param(
            {
                "manifest_rewrites": [
                    {
                        "paths": ["tests/Cargo.toml"],
                        "string_values": [{"table": ["patch", " "]}],
                    }
                ],
            },
            "bump.manifest_rewrites[0].string_values[0].table[1] must not be blank.",
            id="blank_table_segment",
        ),
        pytest.param(
            {
                "manifest_rewrites": [
                    {
                        "paths": ["tests/Cargo.toml"],
                        "string_values": [{"table": ["patch"], "unknown": True}],
                    }
                ],
            },
            "Unknown bump.manifest_rewrites[0].string_values[0] option(s): unknown.",
            id="unknown_string_rule_key",
        ),
        pytest.param(
            {
                "manifest_rewrites": [
                    {
                        "paths": ["tests/Cargo.toml"],
                        "string_values": [{"table": ["patch"], "field": None}],
                    }
                ],
            },
            "bump.manifest_rewrites[0].string_values[0].field must be a string; "
            "received NoneType.",
            id="explicit_null_field",
        ),
    ],
)
def test_bump_config_rejects_invalid_manifest_rewrites(
    mapping: dict[str, object],
    error_message: str,
) -> None:
    """Reject invalid allowlist structures and selector fields."""
    with pytest.raises(
        config_module.ConfigurationError, match=re.escape(error_message)
    ):
        config_module.BumpConfig.from_mapping(mapping)


@pytest.mark.parametrize(
    ("template", "error_message"),
    [
        pytest.param(
            "{crate}",
            "bump.manifest_rewrites[0].string_values[0].template must contain "
            "{version}.",
            id="missing_version",
        ),
        pytest.param(
            "{crate}-{version:>8}",
            "bump.manifest_rewrites[0].string_values[0].template does not support "
            "format specs or conversions.",
            id="format_spec",
        ),
        pytest.param(
            "{crate!r}-{version}",
            "bump.manifest_rewrites[0].string_values[0].template does not support "
            "format specs or conversions.",
            id="conversion",
        ),
        pytest.param(
            "{0}-{version}",
            "bump.manifest_rewrites[0].string_values[0].template only supports "
            "{crate} and {version} fields.",
            id="positional_field",
        ),
        pytest.param(
            "{crate}-{unknown}",
            "bump.manifest_rewrites[0].string_values[0].template only supports "
            "{crate} and {version} fields.",
            id="unknown_field",
        ),
        pytest.param(
            "{crate-{version}",
            "bump.manifest_rewrites[0].string_values[0].template is not a valid "
            "format template.",
            id="malformed_field",
        ),
    ],
)
def test_bump_config_rejects_invalid_manifest_rewrite_templates(
    template: str,
    error_message: str,
) -> None:
    """Only crate and version placeholders without formatting are accepted."""
    mapping = {
        "manifest_rewrites": [
            {
                "paths": ["tests/Cargo.toml"],
                "string_values": [{"table": ["patch"], "template": template}],
            }
        ],
    }

    with pytest.raises(
        config_module.ConfigurationError, match=re.escape(error_message)
    ):
        config_module.BumpConfig.from_mapping(mapping)


def test_preflight_config_from_mapping_trims_and_deduplicates_entries() -> None:
    """Whitespace and duplicate test excludes collapse to unique trimmed values."""
    mapping = {
        "test_exclude": ["  alpha", "beta  ", "", "alpha", "beta", "\tALPHA"],
        "unit_tests_only": False,
    }

    configuration = config_module.PreflightConfig.from_mapping(mapping)

    assert configuration.test_exclude == ("alpha", "beta", "ALPHA")
    assert configuration.unit_tests_only is False


def test_preflight_config_from_mapping_drops_blank_entries() -> None:
    """Blank-only entries are removed entirely."""
    mapping = {"test_exclude": ["", "  ", "\n", "\t"]}

    configuration = config_module.PreflightConfig.from_mapping(mapping)

    assert configuration.test_exclude == ()


def test_use_configuration_sets_context(tmp_path: Path) -> None:
    """The configuration context manager exposes the active configuration."""
    _write_config(tmp_path, "")
    configuration = config_module.load_configuration(tmp_path)

    with pytest.raises(config_module.ConfigurationNotLoadedError):
        config_module.current_configuration()

    with config_module.use_configuration(configuration):
        assert config_module.current_configuration() is configuration


def test_preflight_skip_defaults_to_running_the_checks() -> None:
    """A present but empty ``[preflight]`` table leaves the build checks on.

    The default matters more than most: a publish that silently stopped
    rebuilding and retesting the workspace would still report success. The
    absent-table case is covered by
    ``test_preflight_config_from_mapping_defaults``.
    """
    assert config_module.PreflightConfig.from_mapping({}).skip is False, (
        "an empty [preflight] table must leave the build checks running"
    )


def test_preflight_config_parses_extended_fields() -> None:
    """Aux build commands, externs, and env overrides should be normalized."""
    mapping = {
        "test_exclude": ["alpha", "alpha", "beta"],
        "unit_tests_only": False,
        "aux_build": [["cargo", "fmt"], ["echo", "ok"]],
        "compiletest_extern": {"lint": "target/liblint.so"},
        "env": {"DYLINT_LOCALE": "cy"},
        "stderr_tail_lines": 5,
        "skip": True,
    }

    configuration = config_module.PreflightConfig.from_mapping(mapping)

    assert configuration.aux_build == (("cargo", "fmt"), ("echo", "ok"))
    assert configuration.compiletest_externs == (
        config_module.CompiletestExtern(crate="lint", path="target/liblint.so"),
    )
    assert configuration.env_overrides == (("DYLINT_LOCALE", "cy"),)
    assert configuration.stderr_tail_lines == 5
    assert configuration.test_exclude == ("alpha", "beta")
    assert configuration.unit_tests_only is False
    assert configuration.skip is True


def test_validate_mapping_keys_reports_unknown_section() -> None:
    """Unknown keys in a configuration section should raise a clear error."""
    with pytest.raises(
        config_module.ConfigurationError,
        match=r"Unknown configuration section\(s\): unexpected.",
    ):
        config_module._validate_mapping_keys(
            {"unexpected": True}, set(), "configuration section"
        )


def test_validate_mapping_keys_allows_none_mapping() -> None:
    """A missing mapping should be treated as valid and skipped."""
    config_module._validate_mapping_keys(None, set(), "section")


def test_string_tuple_and_matrix_validation() -> None:
    """String conversion helpers should accept sequences and reject bad types."""
    assert config_module._string_tuple(["a", "b"], "field") == ("a", "b")
    assert config_module._string_matrix([["a", "b"]], "matrix") == (("a", "b"),)
    with pytest.raises(config_module.ConfigurationError):
        config_module._string_tuple(123, "field")
    with pytest.raises(config_module.ConfigurationError):
        config_module._string_matrix("oops", "matrix")
    with pytest.raises(config_module.ConfigurationError):
        config_module._string_matrix([1], "matrix")


def test_string_mapping_and_optional_mapping_validation() -> None:
    """Mapping helpers should normalize values and reject invalid structures."""
    mapping = {"alpha": "one"}
    assert config_module._string_mapping(mapping, "table") == (("alpha", "one"),)
    with pytest.raises(config_module.ConfigurationError):
        config_module._string_mapping("oops", "table")
    with pytest.raises(config_module.ConfigurationError):
        config_module._optional_mapping(["not", "mapping"], "table")


def test_integer_and_boolean_normalization() -> None:
    """Numeric and boolean helpers should enforce allowed shapes."""
    assert config_module._non_negative_int(None, "lines", 3) == 3
    assert config_module._non_negative_int("7", "lines", 0) == 7
    with pytest.raises(config_module.ConfigurationError):
        config_module._non_negative_int(-1, "lines", 0)
    assert config_module._boolean(None, "flag") is False
    assert config_module._boolean(value=True, field_name="flag") is True
    with pytest.raises(config_module.ConfigurationError):
        config_module._boolean("yes", "flag")


def test_strip_patches_rejects_true_and_unknown_values() -> None:
    """Only specific values should be accepted for publish.strip_patches."""
    assert config_module._strip_patches(None) == "per-crate"
    assert config_module._strip_patches(value=False) is False
    assert config_module._strip_patches("all") == "all"
    with pytest.raises(config_module.ConfigurationError):
        config_module._strip_patches(value=True)
    with pytest.raises(config_module.ConfigurationError):
        config_module._strip_patches("unexpected")
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
