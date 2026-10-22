"""Contract tests for ``scripts/format_markdown.py``.

``make fmt`` and ``make check-fmt`` run ``scripts/format_markdown.py``, which
asks the formatter for its own ``--git --include-untracked --list-files``
selection and removes the vendored guides before formatting what remains. The
tests here hold the wrapper to *not* reintroducing a selection of its own -- no
extension filter, no path rewriting, no invocation with an empty file list --
and to the one rule the listing cannot carry: the conflict guard that
``--in-place`` consults.

The formatter is stubbed throughout. A real ``mdtablefix`` would make these
tests an assertion about its behaviour rather than the wrapper's, and the
selection case they care about -- what the wrapper does with whatever the
formatter reported -- is driven entirely by that report.
"""

import json
import shutil
import subprocess
import sys
import typing as typ
from pathlib import Path

import pytest

from tests.helpers.cwd import chdir_for_test
from tests.helpers.script_imports import import_script_module

if typ.TYPE_CHECKING:  # pragma: no cover - typing helper
    import types

#: The name the wrapper defaults to; the stub is passed explicitly instead.
_FORMATTER = "mdtablefix"
#: The wrapper's status for a selection or guard it could not resolve.
_ERROR_STATUS = 2
#: The stub's status for an invocation its spec does not define, so a call the
#: test did not anticipate fails loudly rather than looking like success.
_UNEXPECTED_STATUS = 99

#: Git is run by full path rather than left to ``PATH`` lookup at exec time,
#: which is the same rule the suite's other Git-driving tests follow.
GIT_BINARY = shutil.which("git") or "git"

_STUB_SOURCE = """#!{python}
import json
import pathlib
import sys

spec = json.loads(pathlib.Path({spec!r}).read_text(encoding="utf-8"))
record = pathlib.Path({record!r})
argv = sys.argv[1:]
with record.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(argv) + "\\n")
response = spec.get(argv[0] if argv else "")
if response is None:
    sys.exit({unexpected})
sys.stdout.write(response.get("stdout", ""))
sys.stderr.write(response.get("stderr", ""))
sys.exit(response["status"])
"""

#: A body carrying all three marker forms, which is what ``_has_markers``
#: looks for.
_CONFLICTED = "A\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> side\nB\n"


def _response(status: int, *, stdout: str = "", stderr: str = "") -> dict[str, object]:
    """Return one stub response for a mode."""
    return {"status": status, "stdout": stdout, "stderr": stderr}


def _formatter(
    tmp_path: Path,
    responses: dict[str, dict[str, object]],
) -> tuple[str, Path]:
    """Write a stub formatter with the given per-mode responses.

    Returns
    -------
    tuple[str, Path]
        The stub's path, and the record file it appends each argv line to.
    """
    specification = tmp_path / "formatter.json"
    record = tmp_path / "calls.jsonl"
    specification.write_text(json.dumps(responses), encoding="utf-8")
    executable = tmp_path / _FORMATTER
    executable.write_text(
        _STUB_SOURCE.format(
            python=sys.executable,
            spec=str(specification),
            record=str(record),
            unexpected=_UNEXPECTED_STATUS,
        ),
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return str(executable), record


def _calls(record: Path) -> list[list[str]]:
    """Return every argv the stub recorded, in order."""
    if not record.exists():
        return []
    return [
        typ.cast("list[str]", json.loads(line))
        for line in record.read_text(encoding="utf-8").splitlines()
    ]


def _git(repository: Path, *arguments: str) -> None:
    """Run one Git command in ``repository`` and fail the test if it fails."""
    completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv, no shell
        [GIT_BINARY, *arguments],
        capture_output=True,
        text=True,
        check=False,
        cwd=repository,
        timeout=30,
    )
    assert completed.returncode == 0, (
        f"git {' '.join(arguments)} failed in the fixture repository:\n"
        f"{completed.stdout}{completed.stderr}"
    )


@pytest.fixture(name="module")
def module_fixture(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Import the wrapper as ``uv run --script`` would."""
    return import_script_module(monkeypatch, "format_markdown")


def test_a_selection_failure_is_reported_and_fails_the_run(
    module: types.ModuleType,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A formatter that cannot select files stops the gate with its own message."""
    formatter, record = _formatter(
        tmp_path,
        {"--git": _response(2, stderr="fatal: not a git repository\n")},
    )

    status = module.run([], mode="check", formatter=formatter, vendor=())

    assert status == _ERROR_STATUS, "a failed selection must fail the gate"
    assert "fatal: not a git repository" in capsys.readouterr().err, (
        "the formatter's own diagnostic must reach the user"
    )
    assert _calls(record) == [["--git", "--include-untracked", "--list-files"]], (
        "the formatter must not be asked to format anything after a failed selection"
    )


def test_the_selection_is_forwarded_verbatim_apart_from_vendored_names(
    module: types.ModuleType,
    tmp_path: Path,
) -> None:
    """Every listed file but the vendored ones reaches the formatter.

    ``upper.MD`` and ``docs/guide.md.bak`` are the point: the wrapper adds no
    extension filter and no prefix match of its own, so the case-insensitive
    extension rule and the exact-name exclusion both stay as the formatter and
    the caller defined them.
    """
    listed = "\n".join(
        ["a.md", "upper.MD", "docs/guide.md", "docs/guide.md.bak"],
    )
    formatter, record = _formatter(
        tmp_path,
        {"--git": _response(0, stdout=f"{listed}\n"), "--check": _response(0)},
    )

    status = module.run(
        ["--wrap"],
        mode="check",
        formatter=formatter,
        vendor=["docs/guide.md"],
    )

    assert status == 0, "a clean selection must pass"
    assert _calls(record)[-1] == [
        "--check",
        "--wrap",
        "--",
        "a.md",
        "upper.MD",
        "docs/guide.md.bak",
    ], "the formatter's selection must be forwarded in its own order"


def test_an_empty_selection_passes_without_running_the_formatter(
    module: types.ModuleType,
    tmp_path: Path,
) -> None:
    """Nothing to format is success, not a call with no file arguments."""
    formatter, record = _formatter(tmp_path, {"--git": _response(0, stdout="")})

    status = module.run([], mode="check", formatter=formatter, vendor=())

    assert status == 0, "an empty selection must pass"
    assert _calls(record) == [["--git", "--include-untracked", "--list-files"]], (
        "the formatter must not be invoked with zero positional arguments"
    )


def test_the_formatter_exit_status_is_relayed_unchanged(
    module: types.ModuleType,
    tmp_path: Path,
) -> None:
    """Drift reported by the formatter is drift reported by the wrapper."""
    formatter, _record = _formatter(
        tmp_path,
        {
            "--git": _response(0, stdout="a.md\n"),
            "--check": _response(1, stdout="a.md +1 -1\n"),
        },
    )

    status = module.run([], mode="check", formatter=formatter, vendor=())

    assert status == 1, "the formatter's drift status must be relayed"


def test_a_drifting_conflicted_file_stops_a_rewrite_during_a_pause(
    module: types.ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A paused merge leaves a conflict that ``--in-place`` must refuse to rewrite."""
    _git(tmp_path, "init")
    conflicted = tmp_path / "a.md"
    conflicted.write_text(_CONFLICTED, encoding="utf-8")
    (tmp_path / ".git" / "MERGE_HEAD").write_text("0" * 40 + "\n", encoding="utf-8")
    chdir_for_test(monkeypatch, tmp_path)
    formatter, record = _formatter(
        tmp_path,
        {
            "--git": _response(0, stdout="a.md\n"),
            "--check": _response(1),
            "--in-place": _response(0),
        },
    )

    status = module.run([], mode="in-place", formatter=formatter, vendor=())

    assert status == _ERROR_STATUS, "a guarded rewrite must fail the run"
    assert "refusing to rewrite a.md" in capsys.readouterr().err, (
        "the refusal must name the file it declined to rewrite"
    )
    assert conflicted.read_text(encoding="utf-8") == _CONFLICTED, (
        "the guard must leave the conflicted file untouched"
    )
    assert ["--in-place", "--", "a.md"] not in _calls(record), (
        "the formatter must not be asked to rewrite a refused file"
    )


def test_a_conflicted_file_the_formatter_would_leave_alone_is_not_refused(
    module: types.ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard follows the formatter: it only refuses a rewrite it would make.

    ``mdtablefix`` consults its guard when it is about to write, so a
    conflicted body it would leave unchanged is not a reason to fail. The stub's
    ``--check`` reports no drift, which is the same signal.
    """
    _git(tmp_path, "init")
    (tmp_path / "a.md").write_text(_CONFLICTED, encoding="utf-8")
    (tmp_path / ".git" / "MERGE_HEAD").write_text("0" * 40 + "\n", encoding="utf-8")
    chdir_for_test(monkeypatch, tmp_path)
    formatter, record = _formatter(
        tmp_path,
        {
            "--git": _response(0, stdout="a.md\n"),
            "--check": _response(0),
            "--in-place": _response(0),
        },
    )

    status = module.run([], mode="in-place", formatter=formatter, vendor=())

    assert status == 0, "a file the formatter would not rewrite is not refused"
    assert _calls(record)[-1] == ["--in-place", "--", "a.md"], (
        "the formatter must still be asked to format the file"
    )


def test_no_paused_operation_means_no_guard_check(
    module: types.ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a paused merge there is nothing for the guard to protect."""
    _git(tmp_path, "init")
    (tmp_path / "a.md").write_text(_CONFLICTED, encoding="utf-8")
    chdir_for_test(monkeypatch, tmp_path)
    formatter, record = _formatter(
        tmp_path,
        {"--git": _response(0, stdout="a.md\n"), "--in-place": _response(0)},
    )

    status = module.run([], mode="in-place", formatter=formatter, vendor=())

    assert status == 0, "conflict markers alone must not fail a rewrite"
    assert not any(call[0] == "--check" for call in _calls(record)), (
        "the drift probe is only needed while an operation is paused"
    )


def test_allow_conflicted_is_consumed_and_skips_the_guard(
    module: types.ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The formatter's own opt-out is honoured, not forwarded.

    With an explicit file list ``mdtablefix`` rejects ``--allow-conflicted``
    ("requires --git"), so the wrapper removes it and applies what it means.
    """
    _git(tmp_path, "init")
    (tmp_path / "a.md").write_text(_CONFLICTED, encoding="utf-8")
    (tmp_path / ".git" / "MERGE_HEAD").write_text("0" * 40 + "\n", encoding="utf-8")
    chdir_for_test(monkeypatch, tmp_path)
    formatter, record = _formatter(
        tmp_path,
        {"--git": _response(0, stdout="a.md\n"), "--in-place": _response(0)},
    )

    status = module.run(
        ["--wrap", "--allow-conflicted"],
        mode="in-place",
        formatter=formatter,
        vendor=(),
    )

    assert status == 0, "--allow-conflicted must permit the rewrite"
    assert _calls(record)[-1] == ["--in-place", "--wrap", "--", "a.md"], (
        "the flag must be consumed rather than forwarded"
    )
