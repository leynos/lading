"""Documentation coverage tests for the end-user guide."""

import typing as typ
from pathlib import Path

from lading import config as config_module

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion


def test_users_guide_includes_required_sections(snapshot: SnapshotAssertion) -> None:
    """Ensure the user guide exists and carries the top-level sections."""
    guide_path = Path("docs/users-guide.md")
    content = guide_path.read_text(encoding="utf-8")

    headings = [line for line in content.splitlines() if line.startswith("## ")]

    assert headings == snapshot, "the guide's top-level headings must be unchanged"


def test_users_guide_documents_all_supported_config_keys() -> None:
    """Guard against documentation drift when the config schema changes."""
    content = Path("docs/users-guide.md").read_text(encoding="utf-8")

    supported_keys = set(config_module.BUMP_TOML_KEYS) - {"documentation"}
    supported_keys.update(config_module.BUMP_DOCUMENTATION_TOML_KEYS)
    supported_keys.update(config_module.PUBLISH_TOML_KEYS)
    supported_keys.update(config_module.PREFLIGHT_TOML_KEYS)

    required_terms = (
        "[bump]",
        "[bump.documentation]",
        "[publish]",
        "[preflight]",
        *(f"`{key}`" for key in sorted(supported_keys)),
    )

    missing = [term for term in required_terms if term not in content]
    assert not missing, f"users guide missing terms: {missing}"


def test_users_guide_documents_key_cli_flags_and_env_vars() -> None:
    """Guard against documentation drift for CLI flags and environment variables."""
    content = Path("docs/users-guide.md").read_text(encoding="utf-8")

    required_cli_terms = (
        "lading bump 1.2.3 --dry-run",
        "lading publish --forbid-dirty",
        "lading publish --skip-preflight",
        "lading publish --live",
        "### `--workspace-root`",
    )

    required_env_terms = (
        "`LADING_WORKSPACE_ROOT`",
        "`LADING_LOG_LEVEL`",
        "`LADING_SKIP_PREFLIGHT`",
    )

    missing_cli = [term for term in required_cli_terms if term not in content]
    missing_env = [term for term in required_env_terms if term not in content]

    assert not missing_cli, f"users guide missing CLI terms: {missing_cli}"
    assert not missing_env, f"users guide missing env var terms: {missing_env}"


def test_users_guide_documents_relay_event_contract() -> None:
    """The relay event contract should remain visible to lading users."""
    content = Path("docs/users-guide.md").read_text(encoding="utf-8")

    required_terms = (
        "`relay observability event:`",
        "`operation` is `relay_mirror`",
        "`stream` is `stdout` or `stderr`",
        "`text_to_binary`",
        "`disable_mirroring`",
        "`unicode_encode`",
        "`broken_pipe`",
        "no subprocess payload",
    )
    missing = [term for term in required_terms if term not in content]

    assert not missing, f"users guide missing relay event terms: {missing}"
