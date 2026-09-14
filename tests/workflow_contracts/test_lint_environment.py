"""Contract tests for the Python environments used by lint commands."""

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE_PATH = REPOSITORY_ROOT / "Makefile"
GITIGNORE_PATH = REPOSITORY_ROOT / ".gitignore"


def test_df12_pylint_uses_an_isolated_python_environment() -> None:
    """The CPython 3.14 lint pass must not replace the project virtualenv."""
    makefile = MAKEFILE_PATH.read_text(encoding="utf-8")
    assert "DF12_PYTHON ?= 3.14" in makefile, "DF12_PYLINT must default to CPython 3.14"
    expected = (
        "$(UV) run --isolated --python $(DF12_PYTHON) "
        "--with '$(DF12_PYTHON_LINTS)' pylint"
    )
    assert expected in makefile, (
        "DF12_PYLINT must provision its plugin in an isolated environment"
    )


def test_markdownlint_excludes_project_local_uv_directories() -> None:
    """Generated uv cache and tool documentation must stay out of lint scope."""
    makefile = MAKEFILE_PATH.read_text(encoding="utf-8")
    assert "git ls-files -z '*.md'" in makefile, (
        "markdownlint must lint only tracked files, which excludes every "
        "gitignored path by construction"
    )
    for directory in (".uv-cache", ".uv-tools"):
        entry = f"{directory}/"
        assert entry in GITIGNORE_PATH.read_text(encoding="utf-8"), (
            f"markdownlint must exclude {directory}"
        )
