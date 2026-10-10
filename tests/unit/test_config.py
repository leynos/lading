"""Tests for ``lading.config``."""

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

    assert configuration.bump.exclude == ("internal",), (
        "bump.exclude must load as a tuple of excluded crate names"
    )
    assert configuration.bump.lockfile_manifests == ("crates/nested/Cargo.toml",), (
        "bump.lockfile_manifests must load as a tuple of manifest paths"
    )
    assert configuration.bump.rebuild_lockfiles is False, (
        "an explicit rebuild_lockfiles = false must be honoured"
    )
    assert configuration.bump.documentation.globs == (
        "README.md",
        "docs/**/*.md",
    ), "bump.documentation.globs must preserve the configured glob order"
    assert configuration.publish.exclude == ("examples",), (
        "publish.exclude must load as a tuple of excluded crate names"
    )
    assert configuration.publish.order == ("core",), (
        "publish.order must load as a tuple of crate names"
    )
    assert configuration.publish.strip_patches == "all", (
        'strip_patches = "all" must be accepted as a valid mode'
    )
    assert configuration.preflight.test_exclude == ("cucumber",), (
        "preflight.test_exclude must load as a tuple of crate names"
    )
    assert configuration.preflight.unit_tests_only is True, (
        "an explicit unit_tests_only = true must be honoured"
    )
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

    assert configuration.publish.strip_patches == "per-crate", (
        "strip_patches must default to per-crate when unset"
    )
    assert not configuration.bump.lockfile_manifests, (
        "lockfile_manifests must default to an empty tuple"
    )
    assert configuration.bump.rebuild_lockfiles is True, (
        "rebuild_lockfiles must default to enabled"
    )
    assert not configuration.bump.documentation.globs, (
        "bump.documentation.globs must default to an empty tuple"
    )
    assert configuration.preflight.unit_tests_only is False, (
        "unit_tests_only must default to off"
    )


def test_load_configuration_defaults_without_file(tmp_path: Path) -> None:
    """Missing configuration files fall back to the default configuration."""
    configuration = config_module.load_configuration(tmp_path)

    assert configuration == config_module.LadingConfig(), (
        "a missing lading.toml must yield the default configuration"
    )


def test_preflight_config_from_mapping_parses_fields() -> None:
    """PreflightConfig.from_mapping converts values into tuples and booleans."""
    mapping = {"test_exclude": ["alpha", "beta"], "unit_tests_only": True}

    configuration = config_module.PreflightConfig.from_mapping(mapping)

    assert configuration.test_exclude == ("alpha", "beta"), (
        "test_exclude must normalize the list into an ordered tuple"
    )
    assert configuration.unit_tests_only is True, (
        "a boolean unit_tests_only must be preserved by from_mapping"
    )


def test_preflight_config_from_mapping_defaults() -> None:
    """Missing preflight table falls back to the default configuration."""
    configuration = config_module.PreflightConfig.from_mapping(None)

    assert not configuration.test_exclude, (
        "test_exclude must default to an empty tuple when absent"
    )
    assert configuration.unit_tests_only is False, (
        "unit_tests_only must default to off when absent"
    )
    assert configuration.skip is False, (
        "preflight.skip must default to False when absent"
    )


def test_bump_config_from_mapping_parses_lockfile_fields() -> None:
    """BumpConfig.from_mapping normalizes lockfile settings."""
    mapping = {
        "lockfile_manifests": ["crates/nested/Cargo.toml"],
        "rebuild_lockfiles": True,
    }

    configuration = config_module.BumpConfig.from_mapping(mapping)

    assert configuration.lockfile_manifests == ("crates/nested/Cargo.toml",), (
        "lockfile_manifests must normalize the list into an ordered tuple"
    )
    assert configuration.rebuild_lockfiles is True, (
        "an explicit rebuild_lockfiles = true must be honoured"
    )


def test_bump_config_from_mapping_defaults_lockfile_fields() -> None:
    """Missing bump table falls back to lockfile rebuild defaults."""
    configuration = config_module.BumpConfig.from_mapping(None)

    assert not configuration.lockfile_manifests, (
        "a missing bump table must leave lockfile_manifests empty"
    )
    assert configuration.rebuild_lockfiles is True, (
        "a missing bump table must leave lockfile rebuilding enabled"
    )


def test_preflight_config_from_mapping_trims_and_deduplicates_entries() -> None:
    """Whitespace and duplicate test excludes collapse to unique trimmed values."""
    mapping = {
        "test_exclude": ["  alpha", "beta  ", "", "alpha", "beta", "\tALPHA"],
        "unit_tests_only": False,
    }

    configuration = config_module.PreflightConfig.from_mapping(mapping)

    assert configuration.test_exclude == ("alpha", "beta", "ALPHA"), (
        "entries must be trimmed, blanks dropped, and casings kept distinct"
    )
    assert configuration.unit_tests_only is False, (
        "an explicit unit_tests_only = false must be honoured"
    )


def test_preflight_config_from_mapping_drops_blank_entries() -> None:
    """Blank-only entries are removed entirely."""
    mapping = {"test_exclude": ["", "  ", "\n", "\t"]}

    configuration = config_module.PreflightConfig.from_mapping(mapping)

    assert not configuration.test_exclude, (
        "whitespace-only entries must be dropped, leaving no exclusions"
    )


def test_use_configuration_sets_context(tmp_path: Path) -> None:
    """The configuration context manager exposes the active configuration."""
    _write_config(tmp_path, "")
    configuration = config_module.load_configuration(tmp_path)

    with pytest.raises(config_module.ConfigurationNotLoadedError):
        config_module.current_configuration()

    with config_module.use_configuration(configuration):
        assert config_module.current_configuration() is configuration, (
            "use_configuration must expose the configuration it was given"
        )


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

    assert configuration.aux_build == (("cargo", "fmt"), ("echo", "ok")), (
        "aux_build must normalize command lists into argument tuples"
    )
    assert configuration.compiletest_externs == (
        config_module.CompiletestExtern(crate="lint", path="target/liblint.so"),
    ), "compiletest_extern must map crate names to CompiletestExtern records"
    assert configuration.env_overrides == (("DYLINT_LOCALE", "cy"),), (
        "env must normalize into an ordered tuple of key/value pairs"
    )
    assert configuration.stderr_tail_lines == 5, (
        "an explicit stderr_tail_lines must be preserved"
    )
    assert configuration.test_exclude == ("alpha", "beta"), (
        "duplicate test exclusions must collapse to one entry each"
    )
    assert configuration.unit_tests_only is False, (
        "an explicit unit_tests_only = false must be honoured"
    )
    assert configuration.skip is True, "an explicit skip = true must be honoured"


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
    assert config_module._string_tuple(["a", "b"], "field") == ("a", "b"), (
        "a list of strings must convert to an equivalent tuple"
    )
    assert config_module._string_matrix([["a", "b"]], "matrix") == (("a", "b"),), (
        "a list of string lists must convert to a tuple of tuples"
    )
    with pytest.raises(config_module.ConfigurationError):
        config_module._string_tuple(123, "field")
    with pytest.raises(config_module.ConfigurationError):
        config_module._string_matrix("oops", "matrix")
    with pytest.raises(config_module.ConfigurationError):
        config_module._string_matrix([1], "matrix")


def test_string_mapping_and_optional_mapping_validation() -> None:
    """Mapping helpers should normalize values and reject invalid structures."""
    mapping = {"alpha": "one"}
    assert config_module._string_mapping(mapping, "table") == (("alpha", "one"),), (
        "a string mapping must convert to an ordered tuple of pairs"
    )
    with pytest.raises(config_module.ConfigurationError):
        config_module._string_mapping("oops", "table")
    with pytest.raises(config_module.ConfigurationError):
        config_module._optional_mapping(["not", "mapping"], "table")


def test_integer_and_boolean_normalization() -> None:
    """Numeric and boolean helpers should enforce allowed shapes."""
    assert config_module._non_negative_int(None, "lines", 3) == 3, (
        "an absent value must fall back to the supplied default"
    )
    assert config_module._non_negative_int("7", "lines", 0) == 7, (
        "a numeric string must be parsed into an integer"
    )
    with pytest.raises(config_module.ConfigurationError):
        config_module._non_negative_int(-1, "lines", 0)
    assert config_module._boolean(None, "flag") is False, (
        "an absent boolean must default to False"
    )
    assert config_module._boolean(value=True, field_name="flag") is True, (
        "an explicit True must be returned unchanged"
    )
    with pytest.raises(config_module.ConfigurationError):
        config_module._boolean("yes", "flag")


def test_strip_patches_rejects_true_and_unknown_values() -> None:
    """Only specific values should be accepted for publish.strip_patches."""
    assert config_module._strip_patches(None) == "per-crate", (
        "an absent strip_patches must default to per-crate"
    )
    assert config_module._strip_patches(value=False) is False, (
        "strip_patches = false must be preserved rather than coerced"
    )
    assert config_module._strip_patches("all") == "all", (
        'the supported mode "all" must pass through unchanged'
    )
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
        assert config_module.current_configuration() is config_a, (
            "the outer context must expose the first configuration"
        )
        with config_module.use_configuration(config_b):
            assert config_module.current_configuration() is config_b, (
                "the inner context must expose the second configuration"
            )
        assert config_module.current_configuration() is config_a, (
            "leaving the inner context must restore the outer configuration"
        )

    with pytest.raises(config_module.ConfigurationNotLoadedError):
        config_module.current_configuration()
