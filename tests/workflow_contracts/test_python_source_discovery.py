"""Behavioural contracts for the Python source discovery itself.

``test_lint_environment.py`` holds the discovery *structure* -- that the
predicate exists, that it feeds both gates, and that the fail-closed guard is
spelled with ``$(.SHELLSTATUS)`` and ``$(error``. Structure alone cannot show
that a discovery failure actually stops the build, because a Makefile can
contain all three tokens and still let a broken ``find`` report success. The
tests here run the real ``$(PYTHON_FIND_COMMAND)`` from the repository
Makefile against a controlled fixture tree, so the coverage is of the command
that runs rather than of a copy of its predicate.

The fixture overrides only ``PYTHON_SOURCE_ROOTS``, which the Makefile already
treats as overridable; the command, the prune list and the guard are the
shipped ones. Every invocation passes ``-f`` naming the repository Makefile
explicitly and runs at the repository root, so the fixture directory needs
neither a Makefile nor the project metadata the parse-time detection reads.
"""

import os
import shutil
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE_PATH = REPOSITORY_ROOT / "Makefile"

#: The shebang the discovery predicate matches, as the fixture writes it.
MATCHING_SHEBANG = "#!/usr/bin/env -S uv run python\n"

#: Directories discovery prunes instead of descending into. A contract test
#: holds this equal to the Makefile's own list.
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


def _make_executable() -> str:
    """Return the PATH-resolved ``make`` binary."""
    make = shutil.which("make")
    assert make is not None, "make executable not found on PATH"
    return make


def _makefile_text(*, comments: bool) -> str:
    """Return the Makefile with continuations joined, comments kept or dropped."""
    text = MAKEFILE_PATH.read_text(encoding="utf-8").replace("\\\n", " ")
    if comments:
        return text
    kept = (line for line in text.splitlines() if not line.lstrip().startswith("#"))
    return "\n".join(kept)


def _makefile_assignment(name: str) -> list[str]:
    """Return the whitespace-separated words assigned to ``name``."""
    for line in _makefile_text(comments=True).splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{name} = ") or stripped.startswith(f"{name} ?= "):
            return stripped.split("=", 1)[1].split()
    message = f"{name} is not assigned in the Makefile"
    raise AssertionError(message)


def _run_make(
    argv: list[str], *, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run ``make`` for the repository Makefile with ``argv`` appended."""
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv list using the PATH-resolved make binary
        [_make_executable(), "-f", str(MAKEFILE_PATH), *argv],
        cwd=REPOSITORY_ROOT,
        env=dict(os.environ) | (env or {}),
        capture_output=True,
        text=True,
        check=False,
    )


def _rendered(argv: list[str]) -> str:
    """Return ``make``'s rendered recipe for ``argv`` with continuations joined."""
    completed = _run_make(argv)
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.replace("\\\n", " ")


def _discovered_sources(roots: str) -> list[str]:
    """Return the paths the real discovery command yielded for ``roots``."""
    rendered = _rendered(["-p", "-n", "help", f"PYTHON_SOURCE_ROOTS={roots}"])
    marker = "PYTHON_SOURCES_UNSORTED :="
    for line in rendered.splitlines():
        if line.startswith(marker):
            return line.split(marker, 1)[1].split()
    message = "make did not render PYTHON_SOURCES_UNSORTED; has it been renamed?"
    raise AssertionError(message)


def _write(path: Path, text: str, *, executable: bool = False) -> Path:
    """Write ``text`` to ``path``, creating parents, and return ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if executable:
        path.chmod(0o755)
    return path


def _populate_fixture(root: Path) -> set[str]:
    """Build the fixture tree and return the paths discovery must list."""
    # The tree exercises both predicates and both prune directions: ordinary
    # ``*.py`` files at the top level and nested, a matching extensionless
    # executable, an extensionless non-match, an extensionless file carrying
    # the shebang without being executable, a plain non-Python file, and
    # files beneath each pruned directory in both the ``*.py`` and shebang
    # shapes.
    listed = {
        _write(root / "src" / "main.py", "value = 1\n"),
        _write(root / "src" / "pkg" / "deep.py", "value = 2\n"),
        _write(root / "src" / "legacy.py", "value = 3\n"),
        _write(root / "tools" / "bin" / "deploy", MATCHING_SHEBANG, executable=True),
        _write(root / "tools" / "plain-script", MATCHING_SHEBANG),
    }
    _write(root / "src" / "README.txt", "not Python\n")
    _write(root / "tools" / "bin" / "legacy", "#!/bin/sh\necho no\n", executable=True)
    for directory in PRUNED_DIRECTORIES:
        _write(root / "src" / directory / "pruned.py", "value = 4\n")
        _write(
            root / "tools" / directory / "pruned-script",
            MATCHING_SHEBANG,
            executable=True,
        )
    return {str(path) for path in listed}


def test_a_root_with_no_python_is_not_a_discovery_failure(tmp_path: Path) -> None:
    """A root that matches nothing must discover nothing, not trip the guard.

    The batch ``+`` terminator reports a non-zero utility status, so a shebang
    reader that exits non-zero on a no-match batch would fail every run.
    ``benches`` and ``benchmarks`` are declared roots with no contents, which
    is exactly that case in the real repository.
    """
    empty = tmp_path / "empty"
    empty.mkdir()

    assert _discovered_sources(str(empty)) == [], (
        "a root that matches nothing must discover nothing and must not fail; "
        "the batch terminator reports a non-zero utility status, so a reader "
        "that exits non-zero on a no-match batch would break every run"
    )


def test_discovery_includes_python_files_and_shebang_scripts(
    tmp_path: Path,
) -> None:
    """Both predicates must reach the list, and every exclusion must hold."""
    root = tmp_path / "fixture"
    root.mkdir()
    expected = _populate_fixture(root)

    discovered = set(_discovered_sources(f"{root / 'src'} {root / 'tools'}"))

    missing = expected - discovered
    assert not missing, f"discovery dropped sources it must list: {sorted(missing)}"
    unexpected = discovered - expected
    assert not unexpected, (
        f"discovery listed files it must exclude, including every path beneath "
        f"a pruned directory: {sorted(unexpected)}"
    )


def test_pruned_directories_are_never_descended_into(tmp_path: Path) -> None:
    """No path beneath a pruned directory may reach the discovered list."""
    root = tmp_path / "fixture"
    root.mkdir()
    _populate_fixture(root)

    discovered = _discovered_sources(f"{root / 'src'} {root / 'tools'}")

    leaked = [
        path for path in discovered if set(Path(path).parts) & set(PRUNED_DIRECTORIES)
    ]
    assert not leaked, f"discovery descended into a pruned directory: {leaked}"


def test_the_fixture_prune_coverage_is_complete() -> None:
    """Every pruned directory the Makefile names must appear in the fixture.

    The fixture is what proves each prune term works, so a list that drifted
    behind the Makefile would leave the newest pruned directory untested while
    the suite stayed green.
    """
    declared = set(_makefile_assignment("PYTHON_PRUNED_DIRECTORIES"))
    configured = set(PRUNED_DIRECTORIES)
    assert declared == configured, (
        f"the fixture's prune list must equal the Makefile's; only in "
        f"Makefile: {sorted(declared - configured)}; only in fixture: "
        f"{sorted(configured - declared)}"
    )


def test_a_failing_shebang_reader_stops_the_build(tmp_path: Path) -> None:
    """A reader failure must abort make before any gate command is rendered.

    The stub ``awk`` exits non-zero for every file, which is what an unreadable
    operand does in production. The assertions are causal rather than textual:
    make must exit non-zero, name the failure, and print no gate command,
    because a gate that runs over a truncated list reports green over files it
    never read. Discovery runs at parse time, so the same guard stops every
    target; ``lint`` and ``typecheck`` are the two the abort must cover.
    """
    root = tmp_path / "fixture"
    root.mkdir()
    _populate_fixture(root)
    stub_directory = tmp_path / "stub-bin"
    _write(stub_directory / "awk", "#!/bin/sh\nexit 2\n", executable=True)
    roots = f"PYTHON_SOURCE_ROOTS={root}"
    stubbed = {"PATH": f"{stub_directory}{os.pathsep}{os.environ['PATH']}"}

    for target in ("lint", "typecheck"):
        control = _run_make(["-n", target, roots])
        assert control.returncode == 0, control.stderr
        assert str(root) in control.stdout, (
            f"the control run for `make {target}` must render the fixture "
            f"sources, or the stub run proves nothing about them"
        )

        failed = _run_make(["-n", target, roots], env=stubbed)

        assert failed.returncode != 0, (
            f"a shebang reader that cannot read a file must fail discovery for "
            f"`make {target}`; the guard exists so no gate ever runs over a "
            f"truncated list"
        )
        assert "discovery failed" in failed.stderr, failed.stderr
        for tool in ("ruff", "pylint", "ty check", "interrogate", "skylos"):
            assert tool not in failed.stdout, (
                f"`make {target}` rendered a {tool} command despite a failed "
                f"discovery; the guard must abort before any gate runs"
            )


def test_the_guard_reads_the_batched_child_status() -> None:
    """The predicate must use the ``+`` terminator, not the per-file ``;``.

    ``find`` discards a per-file child's exit status, so an ``awk`` that
    cannot read an operand leaves the walk at zero and the failure is
    indistinguishable from a legitimate non-match. Only the batch terminator
    folds a non-zero utility status back into ``find``'s own, which is what
    the ``$(.SHELLSTATUS)`` guard reads.
    """
    directives = _makefile_text(comments=False)
    assert "{} +" in directives, (
        "discovery must batch its shebang reader with `-exec ... {} +`; with "
        "the per-file terminator find reports success even when the reader "
        "failed"
    )
    assert "{} \\;" not in directives, (
        "the per-file terminator discards the reader's exit status, so the "
        "guard cannot see a read failure"
    )


def test_every_discovered_file_reaches_the_file_list_gates() -> None:
    """The full discovered list must appear on both file-list gate commands.

    Discovery that omits a file and discovery that finds it but never passes it
    to a tool leave the same hole: the gate reports green over a file it never
    read when that file is no longer on the command line. Ruff, Pylint and ty
    all take ``PYTHON_SOURCES``, so each must name every discovered path.
    """
    discovered = set(_discovered_sources("lading tests scripts"))
    assert discovered, "the repository must discover some source"

    for target in ("lint", "typecheck"):
        rendered = _rendered(["-n", target])
        missing = sorted(path for path in discovered if path not in rendered)
        assert not missing, (
            f"`make {target}` does not pass every discovered source to its "
            f"tools: {missing[:5]}"
        )
