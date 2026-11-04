"""Tests for preflight configuration parsing."""

from lading import config as config_module


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
