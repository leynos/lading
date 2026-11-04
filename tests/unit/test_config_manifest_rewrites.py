"""Tests for configured manifest rewrite validation."""

import re
import typing as typ

import pytest

from lading import config as config_module

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion


def test_bump_config_from_mapping_parses_manifest_rewrites(
    snapshot: SnapshotAssertion,
) -> None:
    """Manifest rewrite configuration defaults and selectors are immutable."""
    rewrite_group = {
        "paths": ["tests/fixtures/published-gpui-e2e/Cargo.toml"],
        "string_values": [{"table": ["patch", "crates-io"], "field": "path"}],
    }
    configuration = config_module.BumpConfig.from_mapping({
        "manifest_rewrites": [rewrite_group]
    })

    (rewrite,) = configuration.manifest_rewrites
    (selector,) = rewrite.string_values
    assert (
        rewrite.paths,
        rewrite.dependencies,
        selector.table,
        selector.field,
        selector.template,
    ) == (
        ("tests/fixtures/published-gpui-e2e/Cargo.toml",),
        True,
        ("patch", "crates-io"),
        "path",
        "{crate}-{version}",
    ), "the fixture path, enabled dependencies, and default selector must parse"
    assert configuration.manifest_rewrites == snapshot(
        name="manifest_rewrites_config"
    ), "the parsed immutable records must match the configuration snapshot"


def test_manifest_rewrite_template_allows_escaped_placeholder_text() -> None:
    """Escaped braces remain literal while the actual version field is valid."""
    configuration = config_module.BumpConfig.from_mapping({
        "manifest_rewrites": [
            {
                "paths": ["tests/fixtures/Cargo.toml"],
                "string_values": [
                    {
                        "table": ["patch"],
                        "template": "{{version:}}-{version}",
                    }
                ],
            }
        ],
    })

    rule = configuration.manifest_rewrites[0].string_values[0]
    assert rule.template == "{{version:}}-{version}", (
        "escaped placeholder text must remain literal in a valid template"
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
            "bump.manifest_rewrites[0].paths[0] must be non-blank and have no "
            "leading or trailing whitespace.",
            id="blank_path",
        ),
        pytest.param(
            {"manifest_rewrites": [{"paths": [" tests/Cargo.toml"]}]},
            "bump.manifest_rewrites[0].paths[0] must be non-blank and have no "
            "leading or trailing whitespace.",
            id="padded_path",
        ),
        pytest.param(
            {"manifest_rewrites": [{"paths": ["/workspace/Cargo.toml"]}]},
            "bump.manifest_rewrites[0].paths[0] must be a safe "
            "workspace-relative path.",
            id="absolute_path",
        ),
        pytest.param(
            {"manifest_rewrites": [{"paths": ["C:\\tmp\\Cargo.toml"]}]},
            "bump.manifest_rewrites[0].paths[0] must be a safe "
            "workspace-relative path.",
            id="windows_absolute_path",
        ),
        pytest.param(
            {"manifest_rewrites": [{"paths": ["C:Cargo.toml"]}]},
            "bump.manifest_rewrites[0].paths[0] must be a safe "
            "workspace-relative path.",
            id="windows_drive_relative_path",
        ),
        pytest.param(
            {"manifest_rewrites": [{"paths": ["\\tmp\\Cargo.toml"]}]},
            "bump.manifest_rewrites[0].paths[0] must be a safe "
            "workspace-relative path.",
            id="windows_rooted_path",
        ),
        pytest.param(
            {"manifest_rewrites": [{"paths": ["../outside/Cargo.toml"]}]},
            "bump.manifest_rewrites[0].paths[0] must be a safe "
            "workspace-relative path.",
            id="parent_directory_path",
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
            "bump.manifest_rewrites[0].string_values[0].table[1] must be non-blank "
            "and have no leading or trailing whitespace.",
            id="blank_table_segment",
        ),
        pytest.param(
            {
                "manifest_rewrites": [
                    {
                        "paths": ["tests/Cargo.toml"],
                        "string_values": [{"table": ["patch "]}],
                    }
                ],
            },
            "bump.manifest_rewrites[0].string_values[0].table[0] must be non-blank "
            "and have no leading or trailing whitespace.",
            id="padded_table_segment",
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
        pytest.param(
            {
                "manifest_rewrites": [
                    {
                        "paths": ["tests/Cargo.toml"],
                        "string_values": [{"table": ["patch"], "field": " path"}],
                    }
                ],
            },
            "bump.manifest_rewrites[0].string_values[0].field must be non-blank "
            "and have no leading or trailing whitespace.",
            id="padded_field",
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
            "{crate}-{version:}",
            "bump.manifest_rewrites[0].string_values[0].template does not support "
            "format specs or conversions.",
            id="empty_format_spec",
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
