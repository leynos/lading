"""Contract tests for the single Python baseline the gateways derive from.

``PYTHON_BASELINE`` in the Makefile is the one place the project's Python
version is stated. Everything else -- Ruff's ``target-version``, Pylint's
``py-version``, the interpreters ``uv tool run`` manages, and
``ty --python-version`` -- is a mirror of it, and a mirror that drifts is how
a baseline bump silently becomes a partial one. Raising the baseline while
Ruff still parses at the old version leaves new syntax unlinted; lowering it
while the managed interpreters stay advanced leaves the gate parsing a
language the package no longer promises to run on.

Every assertion here derives its expected spelling from the baseline it reads,
so bumping the value in the Makefile is the whole edit and this module needs
no change. That is the point: a test with its own copy of "3.14" would be one
more mirror to drift.
"""

import collections.abc as cabc
import re
import tomllib
from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE_PATH = REPOSITORY_ROOT / "Makefile"
PYPROJECT_PATH = REPOSITORY_ROOT / "pyproject.toml"


def _makefile() -> str:
    """Return the Makefile with continuations joined into single lines.

    Returns
    -------
    str
        The Makefile's text, with each backslash-newline continuation
        collapsed so a multi-line assignment reads as one line.
    """
    return MAKEFILE_PATH.read_text(encoding="utf-8").replace("\\\n", " ")


def _makefile_variable(name: str) -> str:
    """Return the value assigned to a Makefile variable.

    Parameters
    ----------
    name : str
        The variable name, assigned with ``=`` or ``?=``.

    Returns
    -------
    str
        The assigned value, with surrounding whitespace removed.
    """
    match = re.search(
        rf"^{re.escape(name)}\s*\??=\s*(.+)$", _makefile(), flags=re.MULTILINE
    )
    assert match is not None, f"{name} is not defined in the Makefile"
    return match.group(1).strip()


def test_baseline_is_the_single_source_for_every_gateway() -> None:
    """Each gateway must name ``PYTHON_BASELINE`` rather than its own version."""
    baseline = _makefile_variable("PYTHON_BASELINE")
    assert re.fullmatch(r"\d+\.\d+", baseline), (
        f"PYTHON_BASELINE must be a major.minor version, found {baseline!r}"
    )
    for variable in ("PYLINT_PYTHON", "DF12_PYTHON"):
        assert _makefile_variable(variable) == "$(PYTHON_BASELINE)", (
            f"{variable} must track PYTHON_BASELINE; a private copy is a "
            f"second source of truth that a baseline bump would miss"
        )


def test_gateways_pass_the_baseline_to_their_interpreters() -> None:
    """The managed interpreters and ``ty`` must be told the baseline value."""
    fragments = (
        "--python $(PYLINT_PYTHON)",
        "--python $(DF12_PYTHON)",
        "--python $(PYTHON_BASELINE)",
        "--py-version=$(PYTHON_BASELINE)",
        "--python-version $(PYTHON_BASELINE)",
    )
    makefile = _makefile()
    for fragment in fragments:
        assert fragment in makefile, (
            f"a gateway has stopped passing the baseline to its interpreter: "
            f"missing {fragment!r}"
        )


def test_pyproject_mirrors_the_baseline_for_the_linters() -> None:
    """Ruff and Pylint must parse at the baseline the Makefile declares."""
    baseline = _makefile_variable("PYTHON_BASELINE")
    ruff_tag = "py" + baseline.replace(".", "")
    config = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    assert config["tool"]["ruff"]["target-version"] == ruff_tag, (
        f"Ruff's target-version must be {ruff_tag!r} to match "
        f"PYTHON_BASELINE={baseline}"
    )
    assert config["tool"]["pylint"]["main"]["py-version"] == baseline, (
        f"Pylint's py-version must be {baseline!r} to match PYTHON_BASELINE"
    )


def test_package_metadata_requires_the_baseline() -> None:
    """The declared runtime floor must be the version the gate parses with."""
    baseline = _makefile_variable("PYTHON_BASELINE")
    config = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    requires_python = config["project"]["requires-python"]
    assert requires_python == f">={baseline}", (
        f"requires-python must be >={baseline} to match PYTHON_BASELINE; "
        f"found {requires_python!r}"
    )


def test_lading_is_a_python_source_root() -> None:
    """The production package must be gated in its own right.

    ``PYTHON_SOURCE_ROOTS`` is discovered rather than enumerated, and
    ``lading`` is listed explicitly because it is not reached through any
    other tree. A roots list that omitted it would leave the production
    package entirely ungated while the whole suite still passed.
    """
    roots = _makefile_variable("PYTHON_SOURCE_ROOTS").split()
    assert "lading" in roots, (
        f"the production package must be linted and typechecked as its own "
        f"root, not reached through another tree; roots={roots!r}"
    )


def test_every_workflow_interpreter_is_the_baseline() -> None:
    """Continuous integration, coverage and release must use the baseline.

    The Makefile declares one Python for every gateway, and ``pyproject.toml``
    mirrors it into ``requires-python``. The workflows are a third mirror, and
    the one with the widest blast radius: a lane left on an older interpreter
    still installs the package, because the metadata is what enforces the
    floor, and then fails somewhere unrelated -- or worse, passes while
    measuring a version the project no longer supports. The release lane
    decides what the published wheel is built against, so a stale value there
    ships an artefact for the wrong interpreter.
    """
    baseline = _makefile_variable("PYTHON_BASELINE")
    declarations = _literal_interpreter_declarations()

    assert declarations, "no workflow declares a setup-python version"
    wrong = {name: version for name, version in declarations if version != baseline}
    assert wrong == {}, (
        f"these workflows must declare PYTHON_BASELINE={baseline!r}: {wrong}"
    )


def _step_input(step: cabc.Mapping[str, object], name: str) -> object | None:
    """Return a step's ``with.<name>`` value, or ``None`` when it has none.

    Written as an explicit narrowing rather than ``(step.get("with") or {})``
    because the value coming out of a decoded workflow is ``object``: the
    falsy-then-default idiom leaves a checker with a union it cannot call
    ``.get`` on, and silently accepts a ``with`` block of the wrong shape.

    Parameters
    ----------
    step : Mapping[str, object]
        One decoded workflow step.
    name : str
        The input name to read from the step's ``with`` block.

    Returns
    -------
    object | None
        The input's value, or ``None`` when the step has no ``with`` block or
        no input of that name.
    """
    inputs = step.get("with")
    if not isinstance(inputs, cabc.Mapping):
        return None
    return inputs.get(name)


def _literal_interpreter_declarations() -> list[tuple[str, str]]:
    """Return every workflow's literal ``setup-python`` version.

    Returns
    -------
    list[tuple[str, str]]
        ``(workflow name, version)`` for each step that names a version.
        Steps that forward an expression are excluded, because their value
        comes from somewhere this parser cannot see -- the reusable
        workflow's caller, or a job's own output.
    """
    declarations: list[tuple[str, str]] = []
    workflows = (REPOSITORY_ROOT / ".github" / "workflows").glob("*.yml")
    for path in sorted(workflows):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        for step in _workflow_steps(document):
            if not str(step.get("uses", "")).startswith("actions/setup-python@"):
                continue
            declared_version = _step_input(step, "python-version")
            if declared_version is None:
                continue
            version = str(declared_version)
            if version and not version.startswith("${{"):
                declarations.append((path.name, version))
    return declarations


def test_composite_actions_forward_their_interpreter_input() -> None:
    """A composite action must receive the interpreter, not name its own.

    Both ``.github/actions/pure-python-wheel`` and
    ``.github/actions/build-wheels`` declare a required ``python-version``
    input and set Python up from it. A literal in the action body would be a
    private copy of the baseline that no workflow-level assertion can see, and
    the declared input would quietly stop mattering: callers would go on
    passing an interpreter that nothing reads.
    """
    actions = sorted((REPOSITORY_ROOT / ".github" / "actions").glob("*/action.yml"))
    assert actions, "no composite actions found"
    checked = 0
    for path in actions:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        declared = (document.get("inputs") or {}).get("python-version")
        for step in (document.get("runs") or {}).get("steps") or []:
            if not str(step.get("uses", "")).startswith("actions/setup-python@"):
                continue
            checked += 1
            assert declared is not None, (
                f"{path.parent.name} sets up Python but declares no "
                f"python-version input to get it from"
            )
            passed = _step_input(step, "python-version")
            assert passed == "${{ inputs.python-version }}", (
                f"{path.parent.name} must set Python up from the input it "
                f"declares, not from a value of its own; found {passed!r}"
            )
    assert checked, "no composite action sets up Python"


def test_workflow_steps_pass_the_baseline_to_composite_actions() -> None:
    """A literal handed to a local action must be the baseline.

    ``release.yml`` names the interpreter directly. ``build-wheels.yml`` is a
    reusable workflow that forwards its own ``workflow_call`` input, so the
    value it passes is whatever its caller supplies; that is checked at the
    declaration instead -- a required input with no version default, which is
    the only spelling under which nothing local can drift. What must not
    happen either way is a literal that disagrees with the baseline: the step
    still builds a wheel, for the wrong interpreter.
    """
    baseline = _makefile_variable("PYTHON_BASELINE")
    declarations = (REPOSITORY_ROOT / ".github" / "workflows").glob("*.yml")
    checked = 0
    for path in sorted(declarations):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        call_inputs = _workflow_call_inputs(document)
        for step in _workflow_steps(document):
            uses = str(step.get("uses", ""))
            if not uses.startswith("./.github/actions/"):
                continue
            passed = _step_input(step, "python-version")
            if passed is None:
                continue
            checked += 1
            passed = str(passed)
            if not passed.startswith("${{"):
                assert passed == baseline, (
                    f"{path.name} passes {passed!r} to {uses}; a literal here "
                    f"must be PYTHON_BASELINE={baseline!r}"
                )
                continue
            declared = call_inputs.get("python-version")
            assert isinstance(declared, cabc.Mapping), (
                f"{path.name} forwards {passed!r} to {uses}, but declares no "
                f"python-version workflow_call input for it to resolve to"
            )
            assert declared.get("required") is True, (
                f"{path.name} forwards its python-version input, so it must "
                f"require it rather than let an unset value reach {uses}"
            )
            assert "default" not in declared, (
                f"{path.name} must not default python-version: a default is a "
                f"second copy of the baseline, and this module cannot see it"
            )
    assert checked, "no workflow passes an interpreter to a local action"


def _string_keyed_steps(declared: object) -> list[cabc.Mapping[str, object]]:
    """Return the mapping entries of ``declared`` with string keys.

    A decoded YAML list holds ``object`` members, and ``isinstance(x,
    Mapping)`` narrows only to ``Mapping[Unknown, object]``. ``Mapping`` is
    invariant in its key, so that element type does not satisfy the
    ``Mapping[str, object]`` the step readers are typed against, and a cast
    would assert the key type instead of checking it. Rebuilding each entry
    with ``str`` keys checks it: YAML mappings decoded from ``workflow.yml``
    always carry string keys already, so the rebuild is the identity in
    practice and a non-string key would surface as a changed name rather than
    as a silent pass.

    Parameters
    ----------
    declared : object
        The value a workflow's ``steps`` key held, of unknown shape.

    Returns
    -------
    list[cabc.Mapping[str, object]]
        The step mappings, in order.
    """
    if not isinstance(declared, list):
        return []
    return [
        {str(key): item for key, item in step.items()}
        for step in declared
        if isinstance(step, cabc.Mapping)
    ]


def _workflow_steps(
    document: cabc.Mapping[str, object],
) -> list[cabc.Mapping[str, object]]:
    """Return every step of every job in a decoded workflow.

    Returns
    -------
    list[cabc.Mapping[str, object]]
        The decoded step mappings, in job and step order.
    """
    jobs = document.get("jobs")
    if not isinstance(jobs, cabc.Mapping):
        return []
    steps: list[cabc.Mapping[str, object]] = []
    for job in jobs.values():
        if not isinstance(job, cabc.Mapping):
            continue
        steps.extend(_string_keyed_steps(job.get("steps")))
    return steps


def _workflow_call_inputs(document: cabc.Mapping[str, object]) -> dict[str, object]:
    """Return a workflow's ``workflow_call`` inputs, if it takes any.

    Returns
    -------
    dict[str, object]
        The declared inputs, or an empty mapping for a workflow that is not
        reusable.

    Notes
    -----
    PyYAML resolves the bare key ``on`` to the boolean ``True`` under YAML
    1.1, so the trigger block is looked up under either spelling.
    """
    triggers = document.get("on") or document.get(True)
    if not isinstance(triggers, cabc.Mapping):
        return {}
    call = triggers.get("workflow_call")
    if not isinstance(call, cabc.Mapping):
        return {}
    declared = call.get("inputs")
    if not isinstance(declared, cabc.Mapping):
        return {}
    return {str(name): value for name, value in declared.items()}
