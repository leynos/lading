"""End-to-end tests for the uploader's span record.

The unit tests in ``tests/unit/test_release_span.py`` drive ``record_gh_span``
directly, which proves the record's shape but not that production reaches it.
These run the workflow's own invocation as a subprocess and assert the record
is in the output the job log keeps, which is the only level at which the
process edge is exercised.

The properties asserted here are the same two the unit tests carry, restated
against a run where an argv genuinely exists: the record is emitted on both
the success and the failure path, and it carries nothing from the invocation
that produced it. A record that grew with its data would be a cardinality and
a disclosure problem at once, so the tag, the wheel name, and the directory
are searched for in the serialized line rather than only in the parsed fields.

The run helper and the wheel builder are shared with
``test_upload_release_wheels_cli.py`` and live in
``tests/e2e/helpers/wheel_upload.py``, so neither test module is the other's
library. The no-publication guarantees both rely on are the stub helper's, in
:mod:`tests.helpers.gh_stub`.
"""

import json
from pathlib import Path

import pytest

from tests.e2e.helpers.wheel_upload import make_wheel, run_repository_upload
from tests.helpers.gh_stub import GhStub, install_gh_stub

pytestmark = pytest.mark.timeout(60)


def _span_line(stderr: str) -> dict[str, object] | None:
    """Return the decoded ``release_span`` record from ``stderr``, if any.

    Returns
    -------
    dict[str, object] | None
        The decoded record, or ``None`` when the step emitted none.
    """
    prefix = "release_span "
    for line in stderr.splitlines():
        if line.startswith(prefix):
            return json.loads(line[len(prefix) :])
    return None


def test_a_real_invocation_emits_a_bounded_span_record(
    tmp_path: Path, stub: GhStub
) -> None:
    """The process edge records a span through the real script, not a stub.

    The unit tests drive ``record_gh_span`` in isolation, which proves the
    record's shape but not that production reaches it. This runs the workflow's
    own invocation and asserts the span is in the output the job log keeps.
    """
    dist = tmp_path / "dist"
    make_wheel(dist, "a-1.0-py3-none-any.whl")

    result = run_repository_upload(
        stub,
        "--directory",
        str(dist),
        environment={"GITHUB_REF_NAME": "v1.2.3"},
    )

    assert result.returncode == 0, result.stderr
    record = _span_line(result.stderr)
    assert record is not None, f"no span record in {result.stderr!r}"
    assert record["operation"] == "gh.invoke", record
    assert record["exit_code"] == 0, record
    assert record["failure_category"] == "none", record
    assert set(record) == {
        "operation",
        "schema",
        "duration_seconds",
        "exit_code",
        "failure_category",
    }, record


def test_the_span_carries_no_argument_or_path(tmp_path: Path, stub: GhStub) -> None:
    """The record must not carry the release tag or a wheel's path.

    This is the boundedness property asserted against the real invocation,
    where an argv genuinely exists to leak. A record that grew with its data
    would be a cardinality and a disclosure problem at once, so the tag and the
    wheel name are searched for in the serialized line rather than only the
    parsed fields.
    """
    dist = tmp_path / "dist"
    wheel = make_wheel(dist, "a-1.0-py3-none-any.whl")

    result = run_repository_upload(
        stub,
        "--directory",
        str(dist),
        environment={"GITHUB_REF_NAME": "v9.9.9"},
    )

    assert result.returncode == 0, result.stderr
    line = next(
        (
            line
            for line in result.stderr.splitlines()
            if line.startswith("release_span ")
        ),
        None,
    )
    assert line is not None, f"no span record in {result.stderr!r}"
    assert "v9.9.9" not in line, f"the tag reached the span: {line}"
    assert wheel.name not in line, f"the wheel name reached the span: {line}"
    assert str(dist) not in line, f"the directory reached the span: {line}"


def test_a_failed_invocation_still_emits_a_span(tmp_path: Path) -> None:
    """A rejected upload records its span before the step exits non-zero.

    The interesting path for a trace is the one that failed; a span emitted
    only on success would be missing exactly when it is read.
    """
    stub = install_gh_stub(tmp_path, exit_code=1, stderr="release not found\n")
    dist = tmp_path / "dist"
    make_wheel(dist, "a-1.0-py3-none-any.whl")

    result = run_repository_upload(
        stub,
        "--directory",
        str(dist),
        environment={"GITHUB_REF_NAME": "v1.2.3"},
    )

    assert result.returncode == 1, result.stderr
    record = _span_line(result.stderr)
    assert record is not None, f"no span record in {result.stderr!r}"
    assert record["failure_category"] == "non-zero-exit", record
    assert record["exit_code"] == 1, record
