"""Tests for the release wheel upload script.

The script replaced a shell pipeline that could not fail. These tests hold the
replacement to the behaviour that matters: an empty directory is an error, and
every wheel found reaches ``gh release upload`` with the right tag.
"""

from __future__ import annotations

import ast
import io
import os
import string
import typing as typ
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

try:
    from cmd_mox import CmdMox
except ModuleNotFoundError:  # pragma: no cover - runtime fallback
    CmdMox = typ.Any  # type: ignore[assignment, misc]

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    import collections.abc as cabc
    import types

SCRIPT_DIRECTORY = Path(__file__).resolve().parents[2] / "scripts"
CLI_PATH = SCRIPT_DIRECTORY / "upload_release_wheels.py"
LIBRARY_PATH = SCRIPT_DIRECTORY / "release_wheel_upload.py"


@pytest.fixture(name="upload_module")
def upload_module_fixture(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Import the uploader's logic the way the script imports it.

    The script is run as ``uv run --script scripts/upload_release_wheels.py``,
    which puts ``scripts`` first on the path, so a sibling import resolves.

    Returns
    -------
    types.ModuleType
        The imported ``release_wheel_upload`` module.
    """
    import importlib

    monkeypatch.syspath_prepend(str(SCRIPT_DIRECTORY))
    return importlib.import_module("release_wheel_upload")


def _recorder(
    uploaded: list[tuple[str, tuple[Path, ...]]],
) -> cabc.Callable[[str, cabc.Sequence[Path]], None]:
    """Return an upload that records its arguments instead of running ``gh``.

    Returns
    -------
    cabc.Callable[[str, cabc.Sequence[Path]], None]
        The recording upload.
    """

    def record(tag: str, wheels: cabc.Sequence[Path]) -> None:
        uploaded.append((tag, tuple(wheels)))

    return record


def _make_wheel(directory: Path, name: str) -> Path:
    """Create an empty file named like a wheel and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    wheel = directory / name
    wheel.write_bytes(b"")
    return wheel


@pytest.mark.parametrize("script", [CLI_PATH, LIBRARY_PATH], ids=["cli", "library"])
def test_script_parses_under_the_declared_python_version(script: Path) -> None:
    """Both files must parse under the version the metadata block requires."""
    ast.parse(
        script.read_text(encoding="utf-8"),
        filename=str(script),
        feature_version=(3, 13),
    )


def test_discovery_finds_wheels_in_nested_directories(
    upload_module: types.ModuleType, tmp_path: Path
) -> None:
    """Wheels are found wherever the download action places them.

    The previous shell glob assumed one layout and silently matched nothing
    when the action changed it, so the search is deliberately recursive.
    """
    nested = _make_wheel(tmp_path / "wheels-pure", "b-1.0-py3-none-any.whl")
    top_level = _make_wheel(tmp_path, "a-1.0-py3-none-any.whl")
    _make_wheel(tmp_path, "notes.txt")

    found = upload_module.discover_wheels(tmp_path)

    assert found == (top_level, nested), f"expected both wheels in order: {found}"


def test_discovery_returns_nothing_for_an_empty_directory(
    upload_module: types.ModuleType, tmp_path: Path
) -> None:
    """An empty directory yields no wheels rather than raising."""
    assert upload_module.discover_wheels(tmp_path) == ()


def test_empty_directory_fails_the_step(
    upload_module: types.ModuleType, tmp_path: Path
) -> None:
    """A release with no wheel to attach is an error, not a silent no-op.

    This is the defect the script exists to fix: the shell pipeline it
    replaced exited zero in exactly this case, so two releases published
    without their wheel and the step reported success.
    """
    uploaded: list[tuple[str, tuple[Path, ...]]] = []

    with pytest.raises(upload_module.UploadError, match="No wheel found") as raised:
        upload_module.attach_wheels(
            "v1.2.3",
            tmp_path,
            upload_module.Timings(),
            upload_module.Dependencies(upload=_recorder(uploaded)),
        )

    assert raised.value.outcome == upload_module.Outcome.NO_WHEEL
    assert uploaded == [], "nothing may be uploaded when no wheel was built"


def test_every_wheel_is_uploaded_against_the_tag(
    upload_module: types.ModuleType, tmp_path: Path
) -> None:
    """Each discovered wheel is handed to the upload with the release tag.

    The upload is injected rather than patched onto the module, so the test
    states the dependency it is exercising.
    """
    first = _make_wheel(tmp_path, "a-1.0-py3-none-any.whl")
    second = _make_wheel(tmp_path / "nested", "b-1.0-py3-none-any.whl")
    uploaded: list[tuple[str, tuple[Path, ...]]] = []

    upload_module.attach_wheels(
        "v1.2.3",
        tmp_path,
        upload_module.Timings(),
        upload_module.Dependencies(upload=_recorder(uploaded), log=io.StringIO()),
    )

    assert uploaded == [("v1.2.3", (first, second))]


def test_each_phase_is_timed_by_the_injected_clock(
    upload_module: types.ModuleType, tmp_path: Path
) -> None:
    """Discovery and upload are timed apart, from the clock they are given.

    A single duration could not distinguish a slow artefact tree from a slow
    GitHub, which are the two things the number is read for.
    """
    _make_wheel(tmp_path, "a-1.0-py3-none-any.whl")
    ticks = iter([0.0, 2.0, 10.0, 17.0])
    timings = upload_module.Timings()

    upload_module.attach_wheels(
        "v1.2.3",
        tmp_path,
        timings,
        upload_module.Dependencies(
            upload=_recorder([]), clock=lambda: next(ticks), log=io.StringIO()
        ),
    )

    assert timings.discovery == 2.0, timings
    assert timings.upload == 7.0, timings


def test_the_upload_runner_is_an_injectable_dependency(
    upload_module: types.ModuleType, tmp_path: Path
) -> None:
    """``upload_wheels`` states its process dependency as a parameter.

    The argv can therefore be asserted without intercepting a process, and
    ``--clobber`` is part of it: the release is a reused draft, so a rerun
    after a failed publish would otherwise meet its own asset.
    """
    wheel = _make_wheel(tmp_path, "a-1.0-py3-none-any.whl")
    seen: list[tuple[str, ...]] = []

    def run(arguments: cabc.Sequence[str]) -> object:
        seen.append(tuple(arguments))
        return upload_module.CommandOutcome(exit_code=0)

    upload_module.upload_wheels("v1.2.3", (wheel,), run=run)

    assert seen == [("release", "upload", "v1.2.3", str(wheel), "--clobber")]


def test_a_rejected_upload_names_the_reason(
    upload_module: types.ModuleType, tmp_path: Path
) -> None:
    """A runner that reports failure fails the step with gh's own reason."""
    wheel = _make_wheel(tmp_path, "a-1.0-py3-none-any.whl")

    def run(arguments: cabc.Sequence[str]) -> object:
        return upload_module.CommandOutcome(exit_code=1, stderr="release not found")

    with pytest.raises(upload_module.UploadError, match="release not found") as raised:
        upload_module.upload_wheels("v1.2.3", (wheel,), run=run)

    assert raised.value.outcome == upload_module.Outcome.UPLOAD_FAILED


def test_the_outcome_is_written_to_the_sinks_it_is_given(
    upload_module: types.ModuleType, tmp_path: Path
) -> None:
    """Reporting writes to the sinks passed in, not to ambient streams."""
    log = io.StringIO()
    destination = tmp_path / "github-output"
    summary = upload_module.build_summary(
        upload_module.Outcome.SUCCESS, wheels=2, timings=upload_module.Timings()
    )

    upload_module.report_outcome(
        summary, upload_module.Sinks(log=log, github_output=destination)
    )

    assert '"outcome": "success"' in log.getvalue(), log.getvalue()
    written = destination.read_text(encoding="utf-8")
    assert written == "outcome=success\nwheels=2\n", written


def test_upload_invokes_gh_with_the_tag_and_wheels(
    upload_module: types.ModuleType, tmp_path: Path, cmd_mox: CmdMox
) -> None:
    """The upload crosses the process boundary as one ``gh release upload``.

    Asserted through cmd-mox rather than a stubbed function so the argv the
    release actually runs is the thing under test.
    """
    wheel = _make_wheel(tmp_path, "a-1.0-py3-none-any.whl")
    cmd_mox.mock("gh").with_args(
        "release", "upload", "v1.2.3", str(wheel), "--clobber"
    ).returns(exit_code=0)

    upload_module.upload_wheels("v1.2.3", (wheel,))


def test_failed_upload_raises(
    upload_module: types.ModuleType, tmp_path: Path, cmd_mox: CmdMox
) -> None:
    """A non-zero ``gh`` exit fails the step with the reason attached."""
    wheel = _make_wheel(tmp_path, "a-1.0-py3-none-any.whl")
    cmd_mox.mock("gh").with_args(
        "release", "upload", "v1.2.3", str(wheel), "--clobber"
    ).returns(exit_code=1, stderr="release not found")

    with pytest.raises(upload_module.UploadError, match="release not found"):
        upload_module.upload_wheels("v1.2.3", (wheel,))


def test_run_gh_returns_the_diagnostic_it_captured(
    upload_module: types.ModuleType, cmd_mox: CmdMox
) -> None:
    """The production runner carries ``gh``'s own stderr back to its caller.

    Asserted on ``run_gh`` itself rather than through the error it feeds,
    because the capture is what the record's contract promises: a runner that
    discarded the stream would still produce the right exit code.
    """
    diagnostic = "HTTP 404: release not found"
    cmd_mox.mock("gh").with_args("release", "view").returns(
        exit_code=1, stderr=diagnostic
    )

    outcome = upload_module.run_gh(("release", "view"))

    assert outcome.exit_code == 1, outcome
    assert outcome.stderr.strip() == diagnostic, outcome


def test_missing_directory_is_a_distinct_failure(
    upload_module: types.ModuleType, tmp_path: Path
) -> None:
    """An absent directory reports itself rather than looking empty.

    A download step that never ran and a build that produced nothing are
    different failures, and the operator reading the log needs to tell them
    apart.
    """
    with pytest.raises(upload_module.UploadError, match="does not exist"):
        upload_module.discover_wheels(tmp_path / "absent")


def test_a_file_in_place_of_the_directory_is_rejected(
    upload_module: types.ModuleType, tmp_path: Path
) -> None:
    """A path that is not a directory fails rather than yielding nothing."""
    path = tmp_path / "dist"
    path.write_text("not a directory", encoding="utf-8")

    with pytest.raises(upload_module.UploadError, match="not a directory"):
        upload_module.discover_wheels(path)


requires_unprivileged = pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0,
    reason="permission bits do not restrain this user",
)


@requires_unprivileged
def test_an_unreadable_subtree_is_an_error_not_an_empty_result(
    upload_module: types.ModuleType, tmp_path: Path
) -> None:
    """A subtree the job cannot read fails loudly instead of reading as empty.

    ``Path.rglob`` skips directories it cannot open, so an artefact tree with
    one unreadable branch would otherwise discover nothing and fail with "no
    wheel found" -- the wrong diagnosis of a permissions problem.
    """
    locked = tmp_path / "wheels-pure"
    _make_wheel(locked, "a-1.0-py3-none-any.whl")
    locked.chmod(0o000)
    try:
        with pytest.raises(upload_module.UploadError) as raised:
            upload_module.discover_wheels(tmp_path)
    finally:
        locked.chmod(0o755)

    assert raised.value.outcome == upload_module.Outcome.UNREADABLE_DIRECTORY
    assert "Could not read" in str(raised.value), str(raised.value)


@requires_unprivileged
def test_an_unreadable_artefact_path_is_not_reported_as_absent(
    upload_module: types.ModuleType, tmp_path: Path
) -> None:
    """A stat the job is not permitted to make is a read failure, not absence.

    ``Path.exists`` answers False for a permission error, which would blame the
    download step for a problem it did not cause.
    """
    parent = tmp_path / "locked"
    (parent / "dist").mkdir(parents=True)
    parent.chmod(0o000)
    try:
        with pytest.raises(upload_module.UploadError) as raised:
            upload_module.discover_wheels(parent / "dist")
    finally:
        parent.chmod(0o755)

    assert raised.value.outcome == upload_module.Outcome.UNREADABLE_DIRECTORY


def test_every_outcome_is_drawn_from_the_bounded_set(
    upload_module: types.ModuleType,
) -> None:
    """The outcome label stays a closed set, so a counter built on it is bounded."""
    outcomes = list(upload_module.Outcome)
    assert len({str(outcome) for outcome in outcomes}) == len(outcomes)
    assert upload_module.Outcome.SUCCESS in outcomes
    assert upload_module.UploadError("x").outcome in outcomes


@given(
    names=st.lists(
        st.lists(
            st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=4),
            min_size=1,
            max_size=3,
        ),
        min_size=0,
        max_size=6,
        unique_by=lambda parts: "/".join(parts),
    ),
    suffixes=st.lists(st.sampled_from([".whl", ".txt", ".tar.gz"]), max_size=6),
)
@settings(max_examples=40, deadline=None)
def test_discovery_returns_exactly_the_wheels(
    names: list[list[str]], suffixes: list[str]
) -> None:
    """Every wheel is found, nothing else is, and the order is by path.

    The layout the download action produces is not fixed, so the property is
    stated over arbitrary nesting rather than the one tree an example can show.
    """
    import importlib
    import sys
    import tempfile

    if str(SCRIPT_DIRECTORY) not in sys.path:
        sys.path.insert(0, str(SCRIPT_DIRECTORY))
    module = importlib.import_module("release_wheel_upload")

    with tempfile.TemporaryDirectory() as raw_root:
        root = Path(raw_root)
        expected: list[Path] = []
        for index, parts in enumerate(names):
            suffix = suffixes[index] if index < len(suffixes) else ".whl"
            path = root.joinpath(*parts).with_suffix(suffix)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_dir():
                continue
            path.write_bytes(b"")
            if suffix == ".whl":
                expected.append(path)

        found = module.discover_wheels(root)

    assert found == tuple(sorted(expected)), (
        f"expected exactly the wheels in path order: {found} != {sorted(expected)}"
    )
