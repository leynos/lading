"""Tests for the release wheel upload script.

The script replaced a shell pipeline that could not fail. These tests hold the
replacement to the behaviour that matters: an empty directory is an error, the
upload is a stated dependency, and its outcome reaches the sinks the step is
given. Discovery -- where the wheels are found, and how a directory that cannot
be read is reported -- lives in
``test_upload_release_wheels_discovery.py``.
"""

import ast
import io
import re
import typing as typ
from pathlib import Path

import pytest

from tests.helpers.script_imports import import_script_module

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    import collections.abc as cabc
    import types


SCRIPT_DIRECTORY = Path(__file__).resolve().parents[2] / "scripts"
CLI_PATH = SCRIPT_DIRECTORY / "upload_release_wheels.py"
LIBRARY_PATH = SCRIPT_DIRECTORY / "release_wheel_upload.py"
ADAPTER_PATH = SCRIPT_DIRECTORY / "release_gh.py"
PORT_PATH = SCRIPT_DIRECTORY / "release_port.py"
SPAN_PATH = SCRIPT_DIRECTORY / "release_span.py"


@pytest.fixture(name="release_port")
def release_port_fixture(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Import the uploader's domain port.

    Returns
    -------
    types.ModuleType
        The imported ``release_port`` module.

    Note
    ----
    The port is imported rather than the ``gh`` adapter on purpose. These
    tests exercise filename reconciliation and should not depend on the
    adapter's command or JSON representation.
    """
    return import_script_module(monkeypatch, "release_port")


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
) -> cabc.Callable[[str, cabc.Sequence[Path], object], cabc.Sequence[Path]]:
    """Return an upload that records its arguments instead of running ``gh``.

    Returns
    -------
    cabc.Callable[[str, cabc.Sequence[Path], object], cabc.Sequence[Path]]
        The recording upload.
    """

    def record(
        tag: str, distributions: cabc.Sequence[Path], _log: object
    ) -> cabc.Sequence[Path]:
        uploaded.append((tag, tuple(distributions)))
        return distributions

    return record


def _make_distribution(directory: Path, name: str) -> Path:
    """Create an empty file named like a distribution and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    distribution = directory / name
    distribution.write_bytes(b"")
    return distribution


class _AssetStore:
    """Small domain-port double that records release reconciliation calls."""

    def __init__(
        self,
        attached: tuple[str, ...] = (),
        inspection_error: Exception | None = None,
        upload_error: Exception | None = None,
    ) -> None:
        """Seed one release response and optional operation failures.

        Recorded calls let policy tests assert the reconciliation boundary.
        """
        self.attached = attached
        self.inspection_error = inspection_error
        self.upload_error = upload_error
        self.inspected: list[str] = []
        self.uploads: list[tuple[str, tuple[Path, ...]]] = []

    def attached_names(self, tag: str) -> tuple[str, ...]:
        """Return configured asset names and record the release lookup.

        An injected inspection error models a failed asset query.

        Returns
        -------
        tuple[str, ...]
            The filenames attached to the configured release.
        """
        self.inspected.append(tag)
        if self.inspection_error is not None:
            raise self.inspection_error
        return self.attached

    def upload_missing(
        self, tag: str, distributions: cabc.Sequence[Path]
    ) -> tuple[Path, ...]:
        """Record distribution paths passed to the upload operation.

        An injected upload error models GitHub rejecting those paths.

        Returns
        -------
        tuple[Path, ...]
            The paths accepted by the fake release operation.
        """
        self.uploads.append((tag, tuple(distributions)))
        if self.upload_error is not None:
            raise self.upload_error
        return tuple(distributions)


@pytest.mark.parametrize(
    "script",
    [CLI_PATH, LIBRARY_PATH, ADAPTER_PATH, PORT_PATH, SPAN_PATH],
    ids=["cli", "library", "adapter", "port", "span"],
)
def test_script_parses_under_the_declared_python_version(script: Path) -> None:
    """Every module of the uploader parses at the script block's floor.

    The floor is read from the script's own ``requires-python`` rather than
    written here, because this test and the script block are two statements of
    the same promise: bumping one and not the other would leave a module
    parseable by a release the script refuses to start on, or accepted here
    while the resolver rejects it. The sibling modules are checked against the
    composition root's floor because that is the floor they are imported under.
    """
    source = script.read_text(encoding="utf-8")
    ast.parse(
        source,
        filename=str(script),
        feature_version=_declared_python_floor(CLI_PATH),
    )


def _declared_python_floor(script: Path) -> tuple[int, int]:
    """Return the script block's ``requires-python`` floor.

    Parameters
    ----------
    script : Path
        The script whose PEP 723 block declares the floor.

    Returns
    -------
    tuple[int, int]
        The floor's major and minor components, ready for ``ast.parse``.
    """
    match = re.search(
        r'^# requires-python = ">=(\d+)\.(\d+)"$',
        script.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    assert match is not None, f"{script.name} must declare a requires-python floor"
    return int(match.group(1)), int(match.group(2))


def test_empty_directory_fails_the_step(
    upload_module: types.ModuleType, tmp_path: Path
) -> None:
    """A release with no wheel to attach is an error, not a silent no-op.

    This is the defect the script exists to fix: the shell pipeline it
    replaced exited zero in exactly this case, so two releases published
    without their wheel and the step reported success.
    """
    uploaded: list[tuple[str, tuple[Path, ...]]] = []

    with pytest.raises(
        upload_module.UploadError, match="No distributions found"
    ) as raised:
        upload_module.attach_distributions(
            "v1.2.3",
            tmp_path,
            upload_module.Timings(),
            upload_module.Dependencies(upload=_recorder(uploaded)),
        )

    assert raised.value.outcome == upload_module.Outcome.NO_DISTRIBUTIONS, (
        "an empty artefact directory must report NO_DISTRIBUTIONS"
    )
    assert not uploaded, "nothing may be uploaded when no wheel was built"


def test_every_wheel_is_uploaded_against_the_tag(
    upload_module: types.ModuleType, tmp_path: Path
) -> None:
    """Each discovered wheel is handed to the upload with the release tag.

    The upload is injected rather than patched onto the module, so the test
    states the dependency it is exercising.
    """
    first = _make_distribution(tmp_path, "a-1.0-py3-none-any.whl")
    second = _make_distribution(tmp_path / "nested", "b-1.0-py3-none-any.whl")
    uploaded: list[tuple[str, tuple[Path, ...]]] = []

    upload_module.attach_distributions(
        "v1.2.3",
        tmp_path,
        upload_module.Timings(),
        upload_module.Dependencies(upload=_recorder(uploaded), log=io.StringIO()),
    )

    assert uploaded == [("v1.2.3", (first, second))], (
        "every discovered wheel must upload once against the release tag"
    )


def test_each_phase_is_timed_by_the_injected_clock(
    upload_module: types.ModuleType, tmp_path: Path
) -> None:
    """Discovery and upload are timed apart, from the clock they are given.

    A single duration could not distinguish a slow artefact tree from a slow
    GitHub, which are the two things the number is read for.
    """
    _make_distribution(tmp_path, "a-1.0-py3-none-any.whl")
    ticks = iter([0.0, 2.0, 10.0, 17.0])
    timings = upload_module.Timings()

    upload_module.attach_distributions(
        "v1.2.3",
        tmp_path,
        timings,
        upload_module.Dependencies(
            upload=_recorder([]), clock=lambda: next(ticks), log=io.StringIO()
        ),
    )

    assert timings.discovery == 2.0, timings
    assert timings.upload == 7.0, timings


def test_existing_assets_are_not_uploaded_again(
    upload_module: types.ModuleType, release_port: types.ModuleType, tmp_path: Path
) -> None:
    """A filename already attached to the release is skipped successfully."""
    wheel = _make_distribution(tmp_path, "a-1.0-py3-none-any.whl")
    assets = _AssetStore(attached=(wheel.name,))

    uploaded = upload_module.upload_distributions(
        "v1.2.3", (wheel,), assets=assets, log=io.StringIO()
    )

    assert uploaded == (), "an already-attached distribution should not be uploaded"
    assert assets.inspected == ["v1.2.3"], "the release assets should be inspected once"
    assert assets.uploads == [("v1.2.3", ())], "no paths should be sent for upload"


def test_release_inspection_failure_prevents_distribution_upload(
    upload_module: types.ModuleType, release_port: types.ModuleType, tmp_path: Path
) -> None:
    """A failed release query stops before any upload command is issued."""
    wheel = _make_distribution(tmp_path, "a-1.0-py3-none-any.whl")
    assets = _AssetStore(
        inspection_error=release_port.ReleaseOperationError("permission denied")
    )

    with pytest.raises(upload_module.UploadError, match="permission denied") as raised:
        upload_module.upload_distributions(
            "v1.2.3", (wheel,), io.StringIO(), assets=assets
        )

    assert raised.value.outcome == upload_module.Outcome.UPLOAD_FAILED, (
        "release inspection failures should have the bounded upload outcome"
    )
    assert assets.inspected == ["v1.2.3"], "inspection should fail after one query"
    assert not assets.uploads, "the failed inspection must prevent an upload"


def test_only_missing_distribution_names_are_uploaded(
    upload_module: types.ModuleType, release_port: types.ModuleType, tmp_path: Path
) -> None:
    """A release retry uploads only local filenames absent from its assets."""
    present = _make_distribution(tmp_path, "a-1.0-py3-none-any.whl")
    missing = _make_distribution(tmp_path, "b-1.0.tar.gz")
    assets = _AssetStore(attached=(present.name,))

    uploaded = upload_module.upload_distributions(
        "v1.2.3", (present, missing), assets=assets, log=io.StringIO()
    )

    assert uploaded == (missing,), "only the absent distribution should be uploaded"
    assert assets.uploads == [("v1.2.3", (missing,))], (
        "only the distribution absent from GitHub should be uploaded"
    )


def test_duplicate_distribution_names_fail_before_release_inspection(
    upload_module: types.ModuleType,
    release_port: types.ModuleType,
    tmp_path: Path,
) -> None:
    """Same-named files in different folders cannot map to unique assets."""
    first = _make_distribution(tmp_path / "first", "lading-1.0-py3-none-any.whl")
    second = _make_distribution(tmp_path / "second", first.name)
    assets = _AssetStore()

    with pytest.raises(
        upload_module.UploadError,
        match=re.escape(
            "Duplicate distribution filenames: lading-1.0-py3-none-any.whl"
        ),
    ):
        upload_module.upload_distributions(
            "v1.2.3", (first, second), io.StringIO(), assets=assets
        )

    assert not assets.inspected, "duplicate names should fail before querying GitHub"


def test_a_rejected_upload_names_the_reason(
    upload_module: types.ModuleType, release_port: types.ModuleType, tmp_path: Path
) -> None:
    """A runner that reports failure fails the step with gh's own reason."""
    wheel = _make_distribution(tmp_path, "a-1.0-py3-none-any.whl")

    assets = _AssetStore(
        upload_error=release_port.ReleaseOperationError("release not found")
    )

    with pytest.raises(upload_module.UploadError, match="release not found") as raised:
        upload_module.upload_distributions(
            "v1.2.3", (wheel,), assets=assets, log=io.StringIO()
        )

    assert raised.value.outcome == upload_module.Outcome.UPLOAD_FAILED, (
        "a non-zero gh exit must be reported as the UPLOAD_FAILED outcome"
    )


def test_the_outcome_is_written_to_the_sinks_it_is_given(
    upload_module: types.ModuleType, tmp_path: Path
) -> None:
    """Reporting writes to the sinks passed in, not to ambient streams."""
    log = io.StringIO()
    destination = tmp_path / "github-output"
    summary = upload_module.build_summary(
        upload_module.Outcome.SUCCESS, distributions=2, timings=upload_module.Timings()
    )

    upload_module.report_outcome(
        summary, upload_module.Sinks(log=log, github_output=destination)
    )

    assert '"outcome": "success"' in log.getvalue(), log.getvalue()
    written = destination.read_text(encoding="utf-8")
    assert written == "outcome=success\ndistributions=2\n", written


def test_every_outcome_is_drawn_from_the_bounded_set(
    upload_module: types.ModuleType,
) -> None:
    """The outcome label stays a closed set, so a counter built on it is bounded."""
    outcomes = list(upload_module.Outcome)
    assert len({str(outcome) for outcome in outcomes}) == len(outcomes), (
        "each outcome must carry a distinct label so counters cannot collide"
    )
    assert upload_module.Outcome.SUCCESS in outcomes, (
        "SUCCESS must remain a member of the outcome set"
    )
    assert upload_module.UploadError("x").outcome in outcomes, (
        "an upload error must classify itself with a known outcome"
    )
