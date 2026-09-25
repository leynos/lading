"""Tests for the release wheel upload script.

The script replaced a shell pipeline that could not fail. These tests hold the
replacement to the behaviour that matters: an empty directory is an error, the
upload is a stated dependency, and its outcome reaches the sinks the step is
given. Discovery -- where the wheels are found, and how a directory that cannot
be read is reported -- lives in
``test_upload_release_wheels_discovery.py``.
"""

from __future__ import annotations

import ast
import io
import typing as typ
from pathlib import Path

import pytest

from tests.helpers.script_imports import import_script_module

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
ADAPTER_PATH = SCRIPT_DIRECTORY / "release_gh.py"


@pytest.fixture(name="release_gh")
def release_gh_fixture(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Import the uploader's ``gh`` adapter.

    Returns
    -------
    types.ModuleType
        The imported ``release_gh`` module.
    """
    return import_script_module(monkeypatch, "release_gh")


@pytest.fixture(name="upload_module")
def upload_module_fixture(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Import the uploader's logic the way the script imports it.

    Returns
    -------
    types.ModuleType
        The imported ``release_wheel_upload`` module.
    """
    return import_script_module(monkeypatch, "release_wheel_upload")


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


@pytest.mark.parametrize(
    "script",
    [CLI_PATH, LIBRARY_PATH, ADAPTER_PATH],
    ids=["cli", "library", "adapter"],
)
def test_script_parses_under_the_declared_python_version(script: Path) -> None:
    """Both files must parse under the version the metadata block requires."""
    ast.parse(
        script.read_text(encoding="utf-8"),
        filename=str(script),
        feature_version=(3, 13),
    )


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
    upload_module: types.ModuleType, release_gh: types.ModuleType, tmp_path: Path
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
        return release_gh.CommandOutcome(exit_code=0)

    upload_module.upload_wheels("v1.2.3", (wheel,), run=run)

    assert seen == [("release", "upload", "v1.2.3", str(wheel), "--clobber")]


def test_a_rejected_upload_names_the_reason(
    upload_module: types.ModuleType, release_gh: types.ModuleType, tmp_path: Path
) -> None:
    """A runner that reports failure fails the step with gh's own reason."""
    wheel = _make_wheel(tmp_path, "a-1.0-py3-none-any.whl")

    def run(arguments: cabc.Sequence[str]) -> object:
        return release_gh.CommandOutcome(exit_code=1, stderr="release not found")

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


def test_every_outcome_is_drawn_from_the_bounded_set(
    upload_module: types.ModuleType,
) -> None:
    """The outcome label stays a closed set, so a counter built on it is bounded."""
    outcomes = list(upload_module.Outcome)
    assert len({str(outcome) for outcome in outcomes}) == len(outcomes)
    assert upload_module.Outcome.SUCCESS in outcomes
    assert upload_module.UploadError("x").outcome in outcomes
