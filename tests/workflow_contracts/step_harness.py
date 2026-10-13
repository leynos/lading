"""Select and execute checked-in Bash steps from workflow YAML."""

import os
import subprocess
import typing as typ
from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class WorkflowStep(typ.TypedDict, total=False):
    """One decoded workflow or composite-action step."""

    name: str
    run: str
    env: dict[str, str]
    uses: str
    with_: dict[str, str]


def _as_step(step: object) -> WorkflowStep:
    """Return a decoded step with ``with`` renamed to ``with_``."""
    if not isinstance(step, dict):
        message = f"workflow step must be a mapping, got {step!r}"
        raise TypeError(message)
    return typ.cast(
        "WorkflowStep",
        {("with_" if key == "with" else key): value for key, value in step.items()},
    )


def _steps(path: Path, *, job: str | None = None) -> list[WorkflowStep]:
    """Load steps from one workflow job or composite action."""
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        message = f"{path} must decode to a mapping"
        raise TypeError(message)
    if job is None:
        run_config = document.get("runs", {})
        declared_steps = (
            run_config.get("steps") if isinstance(run_config, dict) else None
        )
    else:
        jobs = document.get("jobs", {})
        selected_job = jobs.get(job) if isinstance(jobs, dict) else None
        declared_steps = (
            selected_job.get("steps") if isinstance(selected_job, dict) else None
        )
    if not isinstance(declared_steps, list):
        location = f"{path}:{job}" if job is not None else str(path)
        message = f"{location} must declare a list of steps"
        raise TypeError(message)
    return [_as_step(step) for step in declared_steps]


def _find_named_step(path: Path, name: str, *, job: str | None = None) -> WorkflowStep:
    """Find a named step in a workflow job or composite action."""
    found = next(
        (step for step in _steps(path, job=job) if step.get("name") == name), None
    )
    if found is None:
        message = f"{path} must contain step {name!r}"
        raise AssertionError(message)
    return found


def _find_step(path: Path, name: str, *, job: str | None = None) -> WorkflowStep:
    """Find a named run step without interpolated GitHub expressions."""
    found = _find_named_step(path, name, job=job)
    script = found.get("run")
    if not isinstance(script, str):
        message = f"step {name!r} must have a run block"
        raise TypeError(message)
    if "${{" in script:
        message = f"step {name!r} must not interpolate expressions into its run block"
        raise ValueError(message)
    return found


def install_recording_command(directory: Path, command: str) -> Path:
    """Put a command stub on ``PATH`` and return its argv record path."""
    binary = directory / "bin"
    binary.mkdir(parents=True, exist_ok=True)
    record = directory / f"{command}-calls.json"
    executable = binary / command
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "pathlib.Path(os.environ['COMMAND_RECORD']).write_text("
        "json.dumps(sys.argv[1:]), encoding='utf-8')\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return record


def _run_step(
    step: WorkflowStep,
    directory: Path,
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one checked-in step with step environment and test overrides."""
    script = step.get("run")
    if not isinstance(script, str):
        message = f"step {step.get('name', '<unnamed>')!r} must have a run block"
        raise TypeError(message)
    if "${{" in script:
        message = f"step {step.get('name', '<unnamed>')!r} has an unsafe expression"
        raise ValueError(message)
    script_path = directory / "workflow-step.sh"
    script_path.write_text(script, encoding="utf-8")
    environment = {**os.environ, **step.get("env", {}), **(env or {})}
    for variable in list(environment):
        if variable.startswith("GIT_CONFIG_") or variable in {
            "BASH_ENV",
            "GIT_SSH",
            "GIT_SSH_COMMAND",
        }:
            environment.pop(variable)
    lody_directory = str(Path.home() / ".lody")
    environment["PATH"] = os.pathsep.join(
        part
        for part in environment.get("PATH", "").split(os.pathsep)
        if part != lody_directory and not part.startswith(lody_directory + os.sep)
    )
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - runs an inspected, checked-in workflow script using a fixed interpreter and argv
        ["/bin/bash", "-e", str(script_path)],
        check=False,
        capture_output=True,
        cwd=directory,
        env=environment,
        text=True,
    )
