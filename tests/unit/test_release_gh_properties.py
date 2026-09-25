"""Capture-fidelity properties for the release uploader's ``gh`` adapter.

``run_gh`` maps a cuprum command result onto the uploader's own
``CommandOutcome``. That mapping is the repository's code, and everything
downstream -- the failure message, the log a maintainer reads at 3 a.m. -- sees
only its output. So the property is stated over arbitrary payloads rather than
the one ASCII line an example would cover: streams may be empty, may hold
invalid UTF-8, CRLF, or NUL, and the status may be any value a process can
report.

The test drives the real adapter against a real child process. A mocked
``CommandResult`` would test the mock, not the contract.
"""

from __future__ import annotations

import os
import sys
import typing as typ
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.helpers.script_imports import import_script_module

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    import types

#: The adapter is stateless -- importing it once per example rather than per
#: input changes nothing -- so the function-scoped-fixture health check is
#: suppressed rather than answered with a module-scoped import that would leak
#: ``sys.path`` across the session.
_SETTINGS = settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

#: The stand-in programme. It echoes the payload it is given, so a test can
#: state the bytes that should come back rather than fishing for them.
_DRIVER = """#!{python}
import pathlib
import sys

payload = pathlib.Path({payload!r})
sys.stdout.buffer.write(payload.joinpath("stdout").read_bytes())
sys.stdout.buffer.flush()
sys.stderr.buffer.write(payload.joinpath("stderr").read_bytes())
sys.stderr.buffer.flush()
sys.exit(int(payload.joinpath("status").read_text(encoding="ascii").strip()))
"""

#: A stand-in that dies by signal, so the adapter meets a negative status.
_SIGNALLED_DRIVER = """#!{python}
import os
import signal

os.kill(os.getpid(), signal.SIGTERM)
"""


@pytest.fixture(name="release_gh")
def release_gh_fixture(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Import the uploader's ``gh`` adapter.

    Returns
    -------
    types.ModuleType
        The imported ``release_gh`` module.
    """
    return import_script_module(monkeypatch, "release_gh")


@pytest.fixture(name="payload_root", scope="module")
def payload_root_fixture(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Return a directory holding the driver and one payload slot.

    Parameters
    ----------
    tmp_path_factory : pytest.TempPathFactory
        Factory for the module-scoped directory. Hypothesis rejects a
        function-scoped fixture, so the driver outlives one example and the
        payload is rewritten for each.

    Returns
    -------
    Path
        The directory holding ``bin/gh`` and the ``payload/`` slot.
    """
    root = tmp_path_factory.mktemp("gh-payload")
    (root / "bin").mkdir()
    payload = root / "payload"
    payload.mkdir()
    (payload / "stdout").write_bytes(b"")
    (payload / "stderr").write_bytes(b"")
    (payload / "status").write_text("0", encoding="ascii")
    _install_driver(root / "bin" / "gh", _DRIVER, payload=str(payload))
    return root


def _install_driver(driver: Path, source: str, **fields: object) -> None:
    """Write an executable stand-in programme."""
    driver.write_text(source.format(python=sys.executable, **fields), encoding="utf-8")
    driver.chmod(0o755)


def _expected(payload: bytes) -> str:
    """Return how cuprum decodes one stream payload."""
    return payload.decode("utf-8", "replace")


def _drive(
    payload_root: Path,
    release_gh: types.ModuleType,
    *,
    stdout: bytes,
    stderr: bytes,
    status: int,
) -> object:
    """Run the adapter against the driver with the given payload.

    Parameters
    ----------
    payload_root : Path
        The directory the ``payload_root`` fixture returned.
    release_gh : types.ModuleType
        The module under test.
    stdout, stderr : bytes
        Bytes the driver writes to that stream.
    status : int
        The status the driver exits with.

    Returns
    -------
    object
        Whatever the adapter returned.
    """
    slot = payload_root / "payload"
    (slot / "stdout").write_bytes(stdout)
    (slot / "stderr").write_bytes(stderr)
    (slot / "status").write_text(str(status), encoding="ascii")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv(
            "PATH",
            f"{payload_root / 'bin'}{os.pathsep}{os.environ['PATH']}",
        )
        return release_gh.run_gh(("release", "view"))


@given(
    stdout=st.binary(max_size=200),
    stderr=st.binary(max_size=200),
    status=st.integers(min_value=0, max_value=255),
)
@_SETTINGS
def test_capture_maps_each_stream_and_the_status(
    payload_root: Path,
    release_gh: types.ModuleType,
    stdout: bytes,
    stderr: bytes,
    status: int,
) -> None:
    """Each stream reaches its own field, decoded as cuprum decodes it.

    The streams are mapped separately rather than concatenated, so a swap of
    the two would be invisible to an assertion that only checked "something was
    captured" -- which is what makes the pairing worth asserting.
    """
    outcome = _drive(
        payload_root, release_gh, stdout=stdout, stderr=stderr, status=status
    )

    assert outcome.exit_code == status, outcome
    assert outcome.stdout == _expected(stdout), outcome
    assert outcome.stderr == _expected(stderr), outcome


@given(
    stdout=st.binary(min_size=1, max_size=200),
    stderr=st.binary(min_size=1, max_size=200),
    status=st.integers(min_value=1, max_value=255),
)
@_SETTINGS
def test_both_streams_are_captured_on_every_failure(
    payload_root: Path,
    release_gh: types.ModuleType,
    stdout: bytes,
    stderr: bytes,
    status: int,
) -> None:
    """Neither stream is discarded, on any failure.

    This is the property the uploader's diagnostics rest on: a runner that
    stopped capturing would still report the right exit code, and the release
    would fail with no reason attached.
    """
    outcome = _drive(
        payload_root, release_gh, stdout=stdout, stderr=stderr, status=status
    )

    assert outcome.stdout == _expected(stdout), outcome
    assert outcome.stderr == _expected(stderr), outcome
    assert outcome.stdout, "stdout was not captured"
    assert outcome.stderr, "stderr was not captured"


@pytest.mark.parametrize(
    ("stdout", "stderr", "status"),
    [
        (b"", b"", 0),
        (b"", b"", 255),
        (b"ok\n", b"HTTP 422: asset exists\n", 1),
        (b"caf\xc3\xa9\n", b"caf\xe9\n", 0),
        (b"a\r\nb\r\n", b"c\r\nd\r\n", 0),
        (b"before\x00after", b"\xff\xfe", 3),
    ],
    ids=[
        "empty",
        "empty-status-255",
        "text",
        "invalid-utf-8",
        "crlf",
        "nul-and-invalid",
    ],
)
def test_representative_payloads_map_exactly(
    payload_root: Path,
    release_gh: types.ModuleType,
    stdout: bytes,
    stderr: bytes,
    status: int,
) -> None:
    """The examples Hypothesis is unlikely to find on its own.

    CRLF and a NUL byte are the two shapes a naive implementation mangles and
    a random generator reaches rarely; the empty stream is the case where a
    ``None`` would leak through as the string ``"None"``.
    """
    outcome = _drive(
        payload_root, release_gh, stdout=stdout, stderr=stderr, status=status
    )

    assert outcome.exit_code == status, outcome
    assert outcome.stdout == _expected(stdout), outcome
    assert outcome.stderr == _expected(stderr), outcome


def test_a_signalled_child_reports_a_negative_status(
    tmp_path: Path, release_gh: types.ModuleType
) -> None:
    """Death by SIGTERM reaches the caller as cuprum reports it.

    A killed ``gh`` -- the runner timing out, or the job cancelling -- is a
    failure the release must report, and its status is negative rather than an
    exit code. The property's domain stops at 255 for the same reason: only a
    signal can produce a larger or negative one.

    The signalled driver goes in its own directory so that replacing the shared
    one cannot affect another example.
    """
    (tmp_path / "bin").mkdir()
    _install_driver(tmp_path / "bin" / "gh", _SIGNALLED_DRIVER)
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "stdout").write_bytes(b"")
    (payload / "stderr").write_bytes(b"")
    (payload / "status").write_text("0", encoding="ascii")

    outcome = _drive(tmp_path, release_gh, stdout=b"", stderr=b"", status=0)

    assert outcome.exit_code == -15, outcome
