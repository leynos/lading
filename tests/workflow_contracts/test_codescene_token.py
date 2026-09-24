"""Contract-test where the CodeScene token may appear in the publisher.

The upload is ``upload-codescene-coverage``, a composite action. A composite
action's nested steps inherit the calling step's environment, so a token bound
in the upload step's ``env``, or the job's, or the workflow's, reaches every
step inside the action. ``coverage-main.yml`` therefore keeps the token out of
every ``env``. A check step publishes only whether the token exists, the
upload's condition reads that output, and the upload takes the token directly
as an input.

The positive half matters as much as the prohibition. Deleting the token
entirely satisfies "no ``env`` holds it" while the upload's guard goes false
and publishing silently stops, so the token must be named exactly twice: in
the check step's command and in the upload's input.
"""

from __future__ import annotations

import collections.abc as cabc
import typing as typ
from pathlib import Path

import yaml

type YamlValue = (
    str | int | float | bool | list[YamlValue] | dict[str, YamlValue] | None
)
type Mapping = dict[str, YamlValue]

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MAIN_COVERAGE_WORKFLOW_PATH = REPOSITORY_ROOT / ".github/workflows/coverage-main.yml"

JOB = "coverage-upload"
CHECK_STEP = "Check CodeScene token availability"
CHECK_STEP_ID = "codescene_token"
#: GitHub evaluates the expression before the shell starts, so the command
#: writes a literal ``true`` or ``false`` and the token enters no process.
CHECK_COMMAND = (
    'echo "available=${{ secrets.CS_ACCESS_TOKEN != \'\' }}" >> "$GITHUB_OUTPUT"'
)
UPLOAD_STEP = "Upload coverage data to CodeScene"
UPLOAD_CREDENTIAL_INPUT = "${{ secrets.CS_ACCESS_TOKEN }}"
AVAILABLE_CONJUNCT = f"steps.{CHECK_STEP_ID}.outputs.available == 'true'"
CREDENTIAL_NAME = "cs_access_token"


def _publisher() -> Mapping:
    """Return the decoded publisher workflow."""
    workflow = yaml.safe_load(MAIN_COVERAGE_WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict), "coverage-main.yml must contain a mapping"
    return typ.cast("Mapping", workflow)


def _mapping(value: YamlValue, what: str) -> Mapping:
    """Return ``value`` as a mapping, failing with ``what`` otherwise."""
    assert isinstance(value, dict), f"{what} must be a mapping, got {value!r}"
    return typ.cast("Mapping", value)


def _steps(workflow: Mapping) -> list[Mapping]:
    """Return the publisher job's steps, in order."""
    job = _mapping(_mapping(workflow.get("jobs"), "jobs").get(JOB), JOB)
    steps = job.get("steps")
    assert isinstance(steps, list), f"{JOB} must declare a steps list"
    return [_mapping(step, f"a {JOB} step") for step in steps]


def _named(steps: list[Mapping], name: str) -> Mapping:
    """Return the one step called ``name``."""
    found = [step for step in steps if step.get("name") == name]
    assert len(found) == 1, f"{JOB} must declare one {name!r} step, found {len(found)}"
    return found[0]


def _strings(value: YamlValue) -> cabc.Iterator[str]:
    """Yield every string in a parsed YAML value, mapping keys included.

    Keys count because an ``env`` entry names the token as a key as readily
    as a value does.

    Yields
    ------
    str
        Each string in document order, a mapping's key before its value.

    Examples
    --------
    >>> list(_strings({"env": {"TOKEN": "x"}, "steps": ["a", 1]}))
    ['env', 'TOKEN', 'x', 'steps', 'a']
    """
    match value:
        case str():
            yield value
        case dict():
            for key, item in value.items():
                yield from _strings(key)
                yield from _strings(item)
        case list():
            for item in value:
                yield from _strings(item)
        case _:
            return


def _environments(workflow: Mapping) -> cabc.Iterator[YamlValue]:
    """Yield the workflow's, every job's, and every step's ``env`` value."""
    yield workflow.get("env")
    for job in _mapping(workflow.get("jobs"), "jobs").values():
        job_mapping = _mapping(job, "a job")
        yield job_mapping.get("env")
        for step in job_mapping.get("steps") or []:
            yield _mapping(step, "a step").get("env")


def test_the_check_step_publishes_availability_and_nothing_else() -> None:
    """Run one command that writes a boolean, unconditionally, with no env.

    A condition on the check would leave its output unset whenever the
    condition was false, so the upload would skip forever, and an ``env`` on
    it would put the token back into an environment.
    """
    steps = _steps(_publisher())
    check = _named(steps, CHECK_STEP)
    assert check.get("id") == CHECK_STEP_ID, check.get("id")
    assert str(check.get("run", "")).strip() == CHECK_COMMAND, check.get("run")
    assert "if" not in check, "the check must run unconditionally"
    assert "env" not in check, "the check must declare no env"
    assert steps.index(check) < steps.index(_named(steps, UPLOAD_STEP)), (
        "the check must run before the upload that reads its output"
    )


def test_the_upload_reads_the_check_and_takes_the_token_directly() -> None:
    """Guard the upload on the check's output and pass the secret as input."""
    upload = _named(_steps(_publisher()), UPLOAD_STEP)
    conjuncts = [part.strip() for part in str(upload.get("if", "")).split("&&")]
    assert AVAILABLE_CONJUNCT in conjuncts, upload.get("if")
    inputs = _mapping(upload.get("with"), "the upload's inputs")
    assert inputs.get("access-token") == UPLOAD_CREDENTIAL_INPUT, inputs.get(
        "access-token"
    )


def test_no_environment_on_the_publisher_holds_the_token() -> None:
    """Keep the token out of the workflow, job and step environments."""
    holders = [
        env
        for env in _environments(_publisher())
        if any(CREDENTIAL_NAME in text.casefold() for text in _strings(env))
    ]
    assert not holders, (
        f"no env on the publisher may name the token, found {holders!r}; a "
        "composite action's nested steps inherit the calling step's env"
    )


def test_the_token_appears_exactly_where_it_is_used() -> None:
    """Name the token in the check's command and the upload's input, only.

    Expression contexts are case-insensitive, so the scan case-folds.
    """
    mentions = sorted(
        text.strip()
        for text in _strings(_publisher())
        if CREDENTIAL_NAME in text.casefold()
    )
    assert mentions == sorted([CHECK_COMMAND, UPLOAD_CREDENTIAL_INPUT]), mentions
