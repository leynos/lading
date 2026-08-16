"""Contract tests for the Python environments used by lint commands."""

from pathlib import Path

MAKEFILE_PATH = Path(__file__).resolve().parents[2] / "Makefile"


def test_df12_pylint_uses_an_isolated_python_environment() -> None:
    """The CPython 3.14 lint pass must not replace the project virtualenv."""
    makefile = MAKEFILE_PATH.read_text(encoding="utf-8")
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
    for directory in (".uv-cache", ".uv-tools"):
        exclusion = f"-not -path './{directory}/*'"
        assert exclusion in makefile, f"markdownlint must exclude {directory}"
