"""Unit tests for the recording command runner helper."""

from tests.helpers.recording_runner import RecordingCommandRunner


def test_runner_copies_the_environment_into_the_journal() -> None:
    """A caller mutating its environment cannot rewrite recorded history."""
    runner = RecordingCommandRunner()
    environment = {"LANG": "C"}

    runner(("git", "ls-files"), env=environment)
    environment["LANG"] = "fr_FR"

    assert runner.calls[0].env == {"LANG": "C"}, (
        "the journal must hold the environment as it was at invocation time"
    )


def test_runner_preserves_an_absent_environment() -> None:
    """An invocation without an environment override records ``None``."""
    runner = RecordingCommandRunner()

    runner(("git", "ls-files"))

    assert runner.calls[0].env is None, (
        "an absent environment must stay absent in the journal"
    )
