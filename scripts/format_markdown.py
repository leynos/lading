#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.14"
# dependencies = ["cuprum==0.2.0b2", "cyclopts>=3"]
# ///
"""Format the repository's Markdown, leaving the vendored guides untouched.

``mdtablefix --git`` selects exactly what this repository wants formatted: it
folds ``md``, ``mdc``, and ``markdown`` to lower case, skips index entries whose
file has gone, refuses symbolic links and paths outside the tree, deduplicates
hard links by canonical name, and, under ``--in-place``, refuses to rewrite a
file carrying conflict markers while a Git operation is paused.

What it cannot do is leave one file out. The two vendored cuprum guides are
imported verbatim from an upstream tag, so their wrapping is upstream's, and
rewriting it would make the copy diverge from the tag it cites. ``mdtablefix``
has no exclude option, and ``--git`` refuses to be combined with an explicit
file list, so this script asks the formatter for its own selection with ``--git
--include-untracked --list-files``, drops the vendored names, and formats what
remains. Every rule above therefore stays the formatter's own, rather than a
copy here that could drift from it.

``--list-files`` reads no file, so the conflict guard it would consult for an
``--in-place`` run is never reached. The script applies that one rule itself,
and only where the formatter would have applied it: a selected file carrying
all three marker forms at the start of a line is refused when the formatter
would rewrite it while the Git directory holds a paused merge, rebase, revert,
or cherry-pick.

The formatter's own ``--allow-conflicted`` needs ``--git``, so it cannot be
forwarded with an explicit file list. It is honoured instead: passing it skips
the guard above, which is exactly what it does to the formatter's own.

The formatter's exit status is relayed unchanged, and a selection that cannot
be resolved exits ``2`` exactly as the formatter's own ``--git`` selection does.
An empty selection is a success, not a reason to read standard input.

Examples
--------
```bash
scripts/format_markdown.py --mode check -- --wrap --renumber
scripts/format_markdown.py --mode in-place --vendor docs/guide.md -- --wrap
```
"""

import collections.abc as cabc
import re
import sys
import typing as typ
from pathlib import Path

import cyclopts
from cuprum import (
    CommandResult,
    Program,
    ProgramCatalogue,
    ProjectSettings,
    RunOutputOptions,
    scoped,
    sh,
)

#: The three marker forms one conflicted hunk carries, in Git's order.
_MARKERS = ("<", "=", ">")
#: The shortest run that begins a marker line; seven is Git's default.
_MARKER_RUN = 7
#: The entries a Git directory holds only while an operation is paused.
_IN_PROGRESS = (
    "MERGE_HEAD",
    "rebase-merge",
    "rebase-apply",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
)
#: One escaped character in a ``--list-files`` line.
_LISTED_ESCAPE = re.compile(r"\\([\\nr])")
#: What each escape stands for, keyed by the character after the backslash.
_LISTED_UNESCAPED = {"\\": "\\", "n": "\n", "r": "\r"}
#: The status the formatter reports for a selection, read, or guard failure.
_ERROR_STATUS = 2
#: The status the formatter reports when ``--check`` finds drift.
_DRIFT_STATUS = 1
#: The formatter's own guard opt-out, consumed rather than forwarded.
_ALLOW_CONFLICTED = "--allow-conflicted"

_GIT = Program("git")


class SelectionError(RuntimeError):
    """The formatter could not say which files the run should act on."""


def _catalogue(*programs: Program) -> ProgramCatalogue:
    """Return a catalogue allowlisting the named programmes for one run."""
    project = ProjectSettings(
        name="lading-format-markdown",
        programs=programs,
        documentation_locations=("docs/developers-guide.md#markdown-linting",),
        noise_rules=(),
    )
    return ProgramCatalogue(projects=(project,))


def _invoke(
    program: Program,
    flags: cabc.Sequence[str],
    *,
    capture: bool,
) -> CommandResult:
    """Run one allowlisted programme, reporting a missing binary as a selection error.

    Returns
    -------
    CommandResult
        The finished run's exit status and, when captured, both streams.

    Raises
    ------
    SelectionError
        If the programme cannot be started.
    """
    catalogue = _catalogue(program)
    try:
        with scoped(catalogue=catalogue):
            command = sh.make(program, catalogue=catalogue)(*flags)
            # Echo relays the child's own report to this process's streams as
            # it arrives; capture alone would hold it until the run had
            # finished, and dropping capture without echo discards it outright.
            return command.run_sync(
                output=RunOutputOptions(capture=capture, echo=not capture),
            )
    except FileNotFoundError as error:
        message = f"cannot run {program}: {error.strerror}"
        raise SelectionError(message) from error


def _captured(program: Program, arguments: cabc.Sequence[str]) -> tuple[int, str, str]:
    """Run one allowlisted programme and return its status and both streams."""
    result = _invoke(program, arguments, capture=True)
    return result.exit_code, result.stdout or "", result.stderr or ""


def _diagnosis(status: int, stderr: str) -> str:
    """Return the child's own diagnostic, falling back to its exit status."""
    return stderr.strip() or f"exit status {status}"


def _unescaped(line: str) -> str:
    """Decode one ``--list-files`` line into the path it names."""
    return _LISTED_ESCAPE.sub(
        lambda match: _LISTED_UNESCAPED[match.group(1)],
        line,
    )


def _listed(formatter: str) -> list[str]:
    """Return the paths the formatter's own repository selection names."""
    status, stdout, stderr = _captured(
        Program(formatter),
        ("--git", "--include-untracked", "--list-files"),
    )
    if status != 0:
        raise SelectionError(_diagnosis(status, stderr))
    return [_unescaped(line) for line in stdout.splitlines()]


def _selected(formatter: str, excluded: cabc.Iterable[str]) -> list[str]:
    """Return the selection with ``excluded`` omitted, in the same order."""
    skipped = set(excluded)
    return [path for path in _listed(formatter) if path not in skipped]


def _has_conflict_markers(text: str) -> bool:
    """Whether ``text`` carries every conflict-marker form at a line start."""
    lines = text.splitlines()
    return all(
        any(line.startswith(marker * _MARKER_RUN) for line in lines)
        for marker in _MARKERS
    )


def _git_output(arguments: cabc.Sequence[str]) -> str:
    """Run one read-only Git query through the catalogue and return stdout."""
    status, stdout, stderr = _captured(_GIT, arguments)
    if status != 0:
        message = f"git {' '.join(arguments)}: {_diagnosis(status, stderr)}"
        raise SelectionError(message)
    return stdout


def _git_directory() -> Path:
    """Return the absolute Git directory governing the working tree."""
    # One terminator, and never a path character: a directory name may end in
    # whitespace of its own, and only the terminator Git added is ours to drop.
    reported = _git_output(("rev-parse", "--absolute-git-dir"))
    stripped = reported.removesuffix("\n").removesuffix("\r")
    if not stripped:
        message = "git rev-parse --absolute-git-dir named no directory"
        raise SelectionError(message)
    return Path(stripped)


def _present(path: Path) -> bool:
    """Whether ``path`` exists, reporting every failure except absence."""
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as error:
        message = f"reading {path}: {error}"
        raise SelectionError(message) from error
    return True


def _operation_paused(git_dir: Path) -> bool:
    """Whether a merge, rebase, revert, or cherry-pick is paused."""
    return any(_present(git_dir / name) for name in _IN_PROGRESS)


def _has_markers(path: str) -> bool:
    """Whether the file at ``path`` carries conflict markers."""
    try:
        return _has_conflict_markers(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        message = f"reading {path}: {error}"
        raise SelectionError(message) from error


def _run_formatter(
    formatter: str,
    mode: str,
    arguments: cabc.Sequence[str],
    *,
    capture: bool,
) -> int:
    """Run the formatter over ``arguments`` and return its exit status."""
    result = _invoke(
        Program(formatter),
        [mode, *arguments],
        capture=capture,
    )
    return result.exit_code


def _drifts(path: str, formatter: str, rules: cabc.Sequence[str]) -> bool:
    """Whether the formatter would rewrite ``path``, by its own ``--check``."""
    status = _run_formatter(
        formatter,
        "--check",
        [*rules, "--", path],
        capture=True,
    )
    if status not in {0, _DRIFT_STATUS}:
        message = f"mdtablefix --check {path}: exit status {status}"
        raise SelectionError(message)
    return status == _DRIFT_STATUS


def _refusal(path: str) -> str:
    """Return the refusal for one file the guard declines to rewrite."""
    return (
        f"refusing to rewrite {path}: it contains conflict markers and a merge, "
        "rebase, revert, or cherry-pick is in progress. Resolve it first."
    )


def _guard(
    paths: cabc.Iterable[str],
    formatter: str,
    rules: cabc.Sequence[str],
) -> None:
    """Refuse a rewrite while a paused operation leaves markers in a file.

    Only a file the formatter would change needs refusing: mdtablefix consults
    its guard when it is about to write, so a conflicted file it would leave
    untouched is not a reason to fail the run.

    Raises
    ------
    SelectionError
        If a conflicted file would be rewritten, or the paused state or
        conflict markers cannot be read.
    """
    marked = [path for path in paths if _has_markers(path)]
    if not marked or not _operation_paused(_git_directory()):
        return
    drifting = [path for path in marked if _drifts(path, formatter, rules)]
    if drifting:
        raise SelectionError("\n".join(_refusal(path) for path in drifting))


def select_and_format(
    rules: cabc.Sequence[str],
    *,
    mode: typ.Literal["check", "in-place"],
    formatter: str,
    vendor: cabc.Sequence[str],
) -> int:
    """Select, guard, and format the repository's Markdown.

    Parameters
    ----------
    rules : collections.abc.Sequence[str]
        The formatter's own flags, collected from the command line after ``--``.
        ``--allow-conflicted`` is consumed here rather than forwarded: the
        formatter defines it only for a selection it made itself, and it
        disables the guard this script applies.
    mode : Literal["check", "in-place"]
        Report drift and fail on it, or rewrite the drifting files.
    formatter : str
        The formatter executable to run, named on ``PATH`` or by absolute path.
    vendor : collections.abc.Sequence[str]
        Selected files to leave untouched.

    Returns
    -------
    int
        The formatter's exit status, or ``2`` when the selection or the guard
        cannot be resolved.
    """
    protected = _ALLOW_CONFLICTED not in rules
    forwarded = [rule for rule in rules if rule != _ALLOW_CONFLICTED]
    paths = _selected(formatter, vendor)
    if not paths:
        return 0
    if mode == "in-place" and protected:
        _guard(paths, formatter, forwarded)
    return _run_formatter(
        formatter,
        f"--{mode}",
        [*forwarded, "--", *paths],
        capture=False,
    )


def run(
    rules: list[str] | None = None,
    *,
    mode: typ.Literal["check", "in-place"],
    formatter: str = "mdtablefix",
    vendor: list[str] | None = None,
) -> int:
    """Run the command line's selection and report its exit status.

    Parameters
    ----------
    rules : list[str] | None
        The formatter's own flags, collected after ``--`` on the command line.
    mode : Literal["check", "in-place"]
        Report drift and fail on it, or rewrite the drifting files.
    formatter : str
        The formatter executable to run.
    vendor : list[str] | None
        Selected files to leave untouched.

    Returns
    -------
    int
        The formatter's exit status, or ``2`` when the selection or the guard
        cannot be resolved.
    """
    try:
        return select_and_format(
            rules or (),
            mode=mode,
            formatter=formatter,
            vendor=vendor or (),
        )
    except SelectionError as error:
        print(f"mdtablefix: {error}", file=sys.stderr)
        return _ERROR_STATUS


_SUMMARY = (__doc__ or "").splitlines()[0]

app = cyclopts.App(name="format-markdown", help=_SUMMARY)
app.default(run)


def main(argv: cabc.Sequence[str] | None = None) -> int:
    """Run the command line and return the process exit status."""
    arguments = sys.argv[1:] if argv is None else list(argv)
    return typ.cast("int", app(arguments))


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
