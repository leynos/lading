"""Documentation coverage tests for the end-user guide."""

from __future__ import annotations

from pathlib import Path


def test_users_guide_includes_required_sections() -> None:
    """Ensure the user guide exists and contains the Phase 4.2 requirements."""
    guide_path = Path("docs/users-guide.md")
    content = guide_path.read_text(encoding="utf-8")

    assert "## Installation" in content
    assert "## Tutorial" in content
    assert "## Configuration reference (`lading.toml`)" in content


def test_users_guide_documents_all_supported_config_keys() -> None:
    """Guard against documentation drift when the config schema changes."""
    content = Path("docs/users-guide.md").read_text(encoding="utf-8")

    required_terms = (
        "[bump]",
        "[bump.documentation]",
        "[publish]",
        "[preflight]",
        "`exclude`",
        "`globs`",
        "`order`",
        "`strip_patches`",
        "`test_exclude`",
        "`unit_tests_only`",
        "`aux_build`",
        "`compiletest_extern`",
        "`env`",
        "`stderr_tail_lines`",
    )

    missing = [term for term in required_terms if term not in content]
    assert not missing, f"users guide missing terms: {missing}"
