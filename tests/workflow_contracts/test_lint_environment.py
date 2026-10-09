"""Contract tests for the Python environments used by lint commands."""

import shutil
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE_PATH = REPOSITORY_ROOT / "Makefile"
GITIGNORE_PATH = REPOSITORY_ROOT / ".gitignore"

#: The extensionless Python source the discovery predicate exists to catch.
EXTENSIONLESS_SOURCE = "scripts/publish-check/bin/cargo"

#: Directories the discovery predicate prunes rather than descends into.
PRUNED_DIRECTORIES = (
    ".git",
    ".venv",
    ".uv-cache",
    ".uv-tools",
    ".hypothesis",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
)


def _makefile_variable(name: str) -> str:
    """Return ``name``'s value as written in the Makefile.

    Reads the assignment rather than the expansion so a roots list can be
    compared against the repository without running ``make``.

    Parameters
    ----------
    name : str
        The variable to read.

    Returns
    -------
    str
        The assigned value with continuations joined and whitespace collapsed.

    Raises
    ------
    AssertionError
        If the Makefile assigns no variable of that name, which means the
        contract is reading a variable that has been renamed or removed.
    """
    text = MAKEFILE_PATH.read_text(encoding="utf-8").replace("\\\n", " ")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{name} =") or stripped.startswith(f"{name} ?="):
            return stripped.split("=", 1)[1].strip()
    message = f"{name} is not assigned in the Makefile"
    raise AssertionError(message)


def _expanded_python_sources() -> list[str]:
    """Return the discovered source list, as ``make`` renders it.

    Returns
    -------
    list[str]
        One entry per discovered Python source, in the order ``make`` holds
        them.

    Raises
    ------
    AssertionError
        If ``make`` is absent, or if it renders no
        ``PYTHON_SOURCES_UNSORTED`` assignment -- either way the discovery
        contract can no longer be checked against a live expansion.
    """
    make = shutil.which("make")
    assert make is not None, "make executable not found on PATH"
    completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv list using the PATH-resolved make binary
        [make, "-p", "-n", "help"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    marker = "PYTHON_SOURCES_UNSORTED :="
    for line in completed.stdout.splitlines():
        if line.startswith(marker):
            return line.split(marker, 1)[1].split()
    message = "make did not render PYTHON_SOURCES_UNSORTED; has it been renamed?"
    raise AssertionError(message)


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


def _tool_command_lines(target: str, tool: str) -> list[str]:
    """Return the expanded recipe lines for ``target`` that invoke ``tool``.

    Parameters
    ----------
    target : str
        Make target whose recipe is expanded with ``make -n``.
    tool : str
        Executable name to match at the start of a command word.

    Returns
    -------
    list[str]
        One entry per matching command, each already unwrapped from Make's
        backslash continuations.
    """
    lines = _expanded_sources(target).splitlines()
    marker = f"{tool} "
    return [line for line in lines if marker in line]


def test_the_extensionless_source_reaches_lint_and_typecheck() -> None:
    """The discovered file must actually appear on the gate command lines."""
    for target in ("lint", "typecheck"):
        assert EXTENSIONLESS_SOURCE in _expanded_sources(target), (
            f"`make {target}` must lint and typecheck {EXTENSIONLESS_SOURCE}; "
            f"a file that is discovered but never passed to a tool is still "
            f"ungated"
        )


def test_ruff_commands_pass_their_discovered_sources_explicitly() -> None:
    """Every Ruff invocation must name the file list, not rely on its default.

    Ruff reads `.` when no path is given, and that default discovers by file
    extension alone. The extensionless shim would be linted by Interrogate,
    Pylint and ty -- every gate that receives ``PYTHON_SOURCES`` -- while Ruff
    reported green over a file it never opened. The assertion is on the
    rendered Ruff command rather than on the recipe text: the path already
    appeared on the Pylint lines before the fix, so a whole-recipe containment
    check passed while Ruff stayed unwired. The gate-specific assertion is what
    fails when Ruff drops the list, which the control run confirms.
    """
    ruff_lines = _tool_command_lines("lint", "ruff") + _tool_command_lines(
        "check-fmt", "ruff"
    )
    assert ruff_lines, "expected Ruff invocations in the lint and check-fmt recipes"
    for line in ruff_lines:
        assert EXTENSIONLESS_SOURCE in line, (
            f"Ruff must receive the discovered source list, so that an "
            f"extensionless source such as {EXTENSIONLESS_SOURCE} is actually "
            f"linted; this invocation does not: {line.strip()}"
        )
        assert "--force-exclude" in line, (
            f"Ruff must keep honouring the configured exclusions now that the "
            f"gated paths are named explicitly; without this flag the "
            f"`[tool.ruff.format]` exemption for "
            f"`tests/bdd/steps/test_bump_steps.py` is bypassed and `check-fmt` "
            f"fails: {line.strip()}"
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


def test_every_existing_source_root_contributes_its_python_files() -> None:
    """Each declared root that holds Python must be represented in the list.

    The roots are discovered rather than enumerated, so dropping one is not a
    visible edit to the file list: the gates simply stop reading that tree
    while every one of them stays green. A root with no Python in it yet is
    legitimate -- ``.github`` and the benchmark trees are declared ahead of
    the files they will hold -- so the assertion is per-root and only for
    roots the repository actually has.
    """
    roots = _makefile_variable("PYTHON_SOURCE_ROOTS").split()
    discovered = set(_expanded_python_sources())

    assert roots, "the roots list must not be empty"
    for root in roots:
        directory = REPOSITORY_ROOT / root
        if not directory.is_dir():
            continue
        expected = {
            str(path.relative_to(REPOSITORY_ROOT))
            for path in directory.rglob("*.py")
            if not any(part in PRUNED_DIRECTORIES for part in path.parts)
        }
        missing = expected - discovered
        assert not missing, (
            f"the {root!r} root contains Python that discovery did not return; "
            f"a root that reaches no files is a gate that reads nothing: "
            f"{sorted(missing)[:5]}"
        )


def test_pruned_directories_are_excluded_from_the_discovered_list() -> None:
    """No path under a pruned directory may reach a gate.

    The prune list is what keeps cached dependencies and build dropouts out.
    A tree that lost its prune would not fail anything -- it would simply hand
    every gate thousands of files it has no business reading, and the lint
    result would depend on whatever happens to be in the cache.
    """
    discovered = _expanded_python_sources()

    leaked = [
        path for path in discovered if set(Path(path).parts) & set(PRUNED_DIRECTORIES)
    ]
    assert not leaked, (
        f"discovery returned files beneath a pruned directory: {leaked[:5]}"
    )


def test_python_sources_discovery_is_fail_closed() -> None:
    """A discovery failure must stop the build, not silently trim the list.

    Piping ``find`` into ``sort`` reports the status of ``sort``, so a
    ``find`` that could not read a root exits zero and the gates run over
    whatever survived. That is the failure this guard exists for: the list is
    still non-empty, so nothing else in the build notices. The check is on
    the Makefile's structure rather than on a triggered failure because
    provoking a real one would mean making a directory unreadable during the
    test.
    """
    makefile = MAKEFILE_PATH.read_text(encoding="utf-8")
    assert "$(.SHELLSTATUS)" in makefile, (
        "discovery must read find's exit status; without it a partial file "
        "list is indistinguishable from a complete one"
    )
    assert "$(error" in makefile, (
        "a non-zero discovery status must abort the build rather than be "
        "reported as a warning"
    )
    assert "| sort" not in makefile, (
        "discovery must not pipe find into sort: a pipeline reports only its "
        "last command's status, which is what makes the failure invisible"
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
