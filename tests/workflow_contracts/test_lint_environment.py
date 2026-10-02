"""Contract tests for the Python environments used by lint commands."""

import shutil
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE_PATH = REPOSITORY_ROOT / "Makefile"
GITIGNORE_PATH = REPOSITORY_ROOT / ".gitignore"

#: The extensionless Python source the discovery predicate exists to catch.
EXTENSIONLESS_SOURCE = "scripts/publish-check/bin/cargo"


def _expanded_sources(target: str) -> str:
    """Return the file list ``make -n <target>`` passes to its tools.

    Returns
    -------
    str
        The expanded command text for ``target``.
    """
    make = shutil.which("make")
    assert make is not None, "make executable not found on PATH"
    completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv list using the PATH-resolved make binary
        [make, "-n", target],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.replace("\\\n", " ")


def test_python_sources_are_discovered_by_suffix_or_shebang() -> None:
    """A Python source that is not ``*.py`` must still reach every gate.

    ``scripts/publish-check/bin/cargo`` is a Python program whose name is its
    interface: the shim only shadows the real binary if it is called ``cargo``
    on ``PATH``. A suffix-only sweep therefore cannot see it, and because
    ``PYTHON_SOURCES`` is a plain variable nothing fails -- the gate simply
    reports green over a file it never read. The Makefile matches the
    executable's ``uv run python`` shebang as a second predicate to close that
    hole; this test fails if the predicate is dropped or narrowed.
    """
    makefile = MAKEFILE_PATH.read_text(encoding="utf-8")
    assert "uv run python" in makefile, (
        "PYTHON_SOURCES must also discover extensionless Python sources by "
        "their shebang, or such a file is gated only by accident"
    )
    assert (REPOSITORY_ROOT / EXTENSIONLESS_SOURCE).is_file(), (
        f"{EXTENSIONLESS_SOURCE} is the extensionless source the shebang "
        f"predicate exists for; update this contract if it moved"
    )


def test_the_extensionless_source_reaches_lint_and_typecheck() -> None:
    """The discovered file must actually appear on the gate command lines."""
    for target in ("lint", "typecheck"):
        assert EXTENSIONLESS_SOURCE in _expanded_sources(target), (
            f"`make {target}` must lint and typecheck {EXTENSIONLESS_SOURCE}; "
            f"a file that is discovered but never passed to a tool is still "
            f"ungated"
        )


def test_df12_pylint_uses_an_isolated_python_environment() -> None:
    """The baseline lint pass must not replace the project virtualenv."""
    makefile = MAKEFILE_PATH.read_text(encoding="utf-8")
    assert "DF12_PYTHON ?= $(PYTHON_BASELINE)" in makefile, (
        "DF12_PYTHON must track PYTHON_BASELINE rather than pinning its own copy"
    )
    # The assignment is continued across lines, so match the command fragment
    # rather than a single rendered line.
    for fragment in (
        "$(UV) run --isolated --python $(DF12_PYTHON)",
        "--with '$(DF12_PYTHON_LINTS)' pylint",
    ):
        assert fragment in makefile, (
            f"DF12_PYLINT must provision its plugin in an isolated environment; "
            f"missing {fragment!r}"
        )


def test_df12_enables_c9112_without_exempting_any_path() -> None:
    """C9112 must run with the rest of the family, over every gated file.

    The rule was briefly split onto its own recipe line so it could carry an
    ``--ignore-paths`` exemption for the pytest-bdd step modules. That
    exemption turned out to have no subject -- no step module keeps ``from
    __future__ import annotations``, because pytest-bdd resolves a step's
    annotations through the defining module's globals and the step modules
    import the names they annotate with. With the exemption gone the split
    bought nothing but a second pass over the same file list, so the rule
    belongs back in the shared enable-list.
    """
    makefile = MAKEFILE_PATH.read_text(encoding="utf-8")
    messages = makefile.split("DF12_PYLINT_MESSAGES = ", 1)[1].split("\n", 1)[0]
    assert "C9112" in messages, (
        "C9112 belongs in the shared enable-list now that it carries no "
        "per-path exemption"
    )
    # Read directives only. The comment above the assignment explains why the
    # exemption was withdrawn and names the flag; asserting on the whole file
    # would fail on that prose rather than on a live exemption.
    directives = "\n".join(
        line for line in makefile.splitlines() if not line.lstrip().startswith("#")
    )
    assert "--ignore-paths" not in directives, (
        "no df12 pass may exempt a path; a gated file that needs C9112 "
        "silenced should drop the future import instead"
    )


def test_df12_python_lints_pins_a_commit_not_a_tag() -> None:
    """A moved tag must not be able to change what the df12 gate runs."""
    makefile = MAKEFILE_PATH.read_text(encoding="utf-8")
    ref_line = next(
        line
        for line in makefile.splitlines()
        if line.startswith("DF12_PYTHON_LINTS_REF ?=")
    )
    ref = ref_line.split("?=", 1)[1].strip()
    is_hex = all(character in "0123456789abcdef" for character in ref)
    assert len(ref) == 40, (
        f"DF12_PYTHON_LINTS_REF must be a full commit SHA, found {ref!r}"
    )
    assert is_hex, f"DF12_PYTHON_LINTS_REF must be hexadecimal, found {ref!r}"


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
