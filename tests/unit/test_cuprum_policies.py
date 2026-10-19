"""Pin the cuprum 0.2.0 policies lading's subprocess contract depends on.

lading's runtime runner is still subprocess-based (roadmap Phase 5.2), so
these tests exercise cuprum directly rather than through production code.
They exist because two behaviours lading already guarantees were not
expressible on cuprum 0.1.x: a child environment that contains exactly the
supplied keys, and a parent sink whose reader has gone away without losing
the child's captured output. Both are now opt-in cuprum APIs, and each test
below fails if the opt-in is dropped or the semantics regress to 0.1.x.
"""

import collections.abc as cabc
import io
import json
import sys
import typing as typ

import pytest
from cuprum import (
    BrokenPipePolicy,
    EchoErrorCategory,
    EchoStream,
    ExecutionContext,
    Program,
    ProgramCatalogue,
    ProjectSettings,
    RelayFallback,
    RunOutputOptions,
    scoped,
    sh,
)
from cuprum.context import EnvMode

from lading.runtime.subprocess_runner import normalize_environment
from lading.utils.process import c_locale_env

if typ.TYPE_CHECKING:
    from cuprum import CommandResult
    from cuprum.sinks import OutputSession, SessionOutcome, SessionStart

#: The interpreter running the test-suite, so the child needs no resolution
#: through ``PATH`` and the catalogue admits exactly one executable.
_PYTHON = Program(sys.executable)
_CATALOGUE = ProgramCatalogue(
    projects=(
        ProjectSettings(
            name="lading-cuprum-policies",
            programs=(_PYTHON,),
            documentation_locations=("docs/developers-guide.md",),
            noise_rules=(),
        ),
    )
)

#: A parent variable that must not survive an exact child environment. Its
#: value is only meaningful in the parent, so seeing it in the child proves
#: inheritance rather than a value the test supplied.
_SENTINEL_VARIABLE = "LADING_CUPRUM_PARENT_SENTINEL"

#: Actions variables the uploader deliberately omits when a run does not
#: supply them; a CI parent would otherwise put them back.
_OMITTED_VARIABLES = ("GITHUB_REF_NAME", "GITHUB_OUTPUT")

_ENVIRONMENT_CHILD = (
    "import json, os; print(json.dumps(dict(os.environ), sort_keys=True))"
)
_ECHO_CHILD = "import sys; sys.stdout.write('out\\n'); sys.stderr.write('err\\n')"


class _BrokenPipeWriter(io.TextIOBase):
    """A parent destination whose reader has closed its end of the pipe."""

    def __init__(self) -> None:
        """Start a writer that records how often echo reached it."""
        super().__init__()
        self.write_attempts = 0

    def write(self, payload: str, /) -> int:
        """Refuse the write as a closed pipe would, recording the attempt."""
        del payload
        self.write_attempts += 1
        raise BrokenPipeError

    def flush(self) -> None:
        """Refuse the flush as a closed pipe would."""
        raise BrokenPipeError

    def close(self) -> None:
        """Close without flushing, so interpreter teardown cannot re-raise.

        ``io.IOBase.__del__`` calls ``close()``, and the inherited ``close``
        flushes. An unraisable ``BrokenPipeError`` at garbage collection would
        be attributed to whichever test happened to drop the last reference.
        """


class _RecordingSession:
    """One presentation session for a run whose sink cannot accept output."""

    def __init__(self, writer: _BrokenPipeWriter) -> None:
        """Bind the session to the writer it exposes as its log."""
        self._writer = writer
        self.is_closed = False
        self.outcome: SessionOutcome | None = None

    @property
    def log(self) -> typ.IO[str]:
        """The destination echoed output is routed through."""
        # ``IO`` is nominal in typeshed; the double is duck-typed.
        return typ.cast("typ.IO[str]", self._writer)

    def close(self, outcome: SessionOutcome) -> None:
        """Record the terminal report without retrying a failed write."""
        self.outcome = outcome
        self.is_closed = True


class _BrokenPipeSink:
    """A conforming sink whose destination has gone away.

    It deliberately has no ``buffer`` attribute: cuprum bypasses a sink that
    exposes one and writes raw bytes, so a byte-buffer double would never
    reach the text path under test.
    """

    def __init__(self) -> None:
        """Create a writer and no session."""
        self.writer = _BrokenPipeWriter()
        self.session: _RecordingSession | None = None

    def open_session(self, start: SessionStart) -> OutputSession | None:
        """Open one session for the run and record it."""
        del start
        self.session = _RecordingSession(self.writer)
        return self.session


def _run_child(
    source: str,
    *,
    output: RunOutputOptions | None = None,
    env: cabc.Mapping[str, str] | None = None,
    env_mode: EnvMode = EnvMode.OVERLAY,
) -> CommandResult:
    """Run ``source`` in a child interpreter under the test catalogue."""
    with scoped(catalogue=_CATALOGUE):
        command = sh.make(_PYTHON, catalogue=_CATALOGUE)("-c", source)
        return command.run_sync(
            output=output or RunOutputOptions(capture=True),
            context=ExecutionContext(env=env, env_mode=env_mode),
        )


def _read_child_environment(
    supplied: cabc.Mapping[str, str],
    mode: EnvMode,
) -> dict[str, str]:
    """Return the environment the child reported under ``mode``."""
    result = _run_child(_ENVIRONMENT_CHILD, env=supplied, env_mode=mode)
    assert result.exit_code == 0, result.stderr
    assert result.stdout is not None, "the child produced no output"
    return json.loads(result.stdout)


def _broken_pipe_output(
    sink: _BrokenPipeSink, policy: BrokenPipePolicy
) -> RunOutputOptions:
    """Return the output options for echoing into a closed destination."""
    return RunOutputOptions(
        capture=True,
        echo=True,
        echo_stderr=False,
        sink=sink,
        broken_pipe_policy=policy,
    )


def test_replace_mode_passes_only_the_supplied_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replacement reproduces the runner's existing ``Popen(env=...)`` contract.

    The parent is given the sentinel and the Actions variables first, so a
    child that reports them is inheriting them rather than echoing arguments.
    """
    monkeypatch.setenv(_SENTINEL_VARIABLE, "inherited-from-parent")
    for variable in _OMITTED_VARIABLES:
        monkeypatch.setenv(variable, "set-by-the-parent")
    supplied = c_locale_env(normalize_environment({"LADING_SUPPLIED": "yes"}))

    child = _read_child_environment(supplied, EnvMode.REPLACE)

    assert set(child) == {"LADING_SUPPLIED", "LC_ALL", "LANG", "LANGUAGE"}, child
    assert child["LADING_SUPPLIED"] == "yes", child
    assert (child["LC_ALL"], child["LANG"], child["LANGUAGE"]) == ("C", "C", ""), child


def test_overlay_mode_inherits_the_parent_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default mode leaks the parent, so the replacement test can fail.

    This is the 0.1.x behaviour: a complete dictionary does not remove
    unlisted parent variables. Asserting it here keeps the neighbouring test
    falsifiable rather than vacuous.
    """
    monkeypatch.setenv(_SENTINEL_VARIABLE, "inherited-from-parent")
    for variable in _OMITTED_VARIABLES:
        monkeypatch.setenv(variable, "set-by-the-parent")
    supplied = c_locale_env(normalize_environment({"LADING_SUPPLIED": "yes"}))

    child = _read_child_environment(supplied, EnvMode.OVERLAY)

    assert child[_SENTINEL_VARIABLE] == "inherited-from-parent", child
    for variable in _OMITTED_VARIABLES:
        assert child[variable] == "set-by-the-parent", child
    assert child["LADING_SUPPLIED"] == "yes", child


def test_best_effort_keeps_capture_when_the_sink_breaks() -> None:
    """A closed reader stops echo while capture and the exit code survive.

    This is lading's relay policy expressed natively: one failed write
    disables that stream's mirroring, later chunks are not retried, and the
    child's full output still returns in the result.
    """
    sink = _BrokenPipeSink()

    result = _run_child(
        _ECHO_CHILD,
        output=_broken_pipe_output(sink, BrokenPipePolicy.BEST_EFFORT),
    )

    assert result.exit_code == 0, result.stderr
    assert result.stdout == "out\n", result.stdout
    assert result.stderr == "err\n", result.stderr
    assert result.relay_fallbacks == (
        RelayFallback(
            stream=EchoStream.STDOUT,
            error_category=EchoErrorCategory.BROKEN_PIPE,
        ),
    ), result.relay_fallbacks
    assert sink.writer.write_attempts == 1, "echo must not retry a failed write"
    assert sink.session is not None, "the run opened no session"
    assert sink.session.is_closed, "the session must be closed on every path"


def test_strict_broken_pipe_policy_propagates() -> None:
    """The default policy is strict, which is why the opt-in is load-bearing.

    A future reader might reasonably assume BEST_EFFORT is the default; the
    raise here records that it is not, and that the neighbouring test proves
    the opt-in rather than an unchanged default.
    """
    sink = _BrokenPipeSink()

    with pytest.raises(BrokenPipeError):
        _run_child(
            _ECHO_CHILD,
            output=_broken_pipe_output(sink, BrokenPipePolicy.STRICT),
        )

    assert sink.writer.write_attempts == 1, "echo must not retry a failed write"
