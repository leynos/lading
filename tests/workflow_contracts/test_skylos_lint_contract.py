"""Contract test the Skylos lint gate, whitelist helper, and CI bootstrap.

The Makefile is parsed with Makeutil so the test validates structured variable
and recipe contracts rather than incidental formatting. Run this suite with:

    uv run pytest tests/workflow_contracts/test_skylos_lint_contract.py
"""

from __future__ import annotations

import json
import shlex
import subprocess
import tomllib
import typing as typ
from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_MAKEUTIL_COMMAND: typ.Final = ("makeutil", "parse", "Makefile")
_MAKEUTIL_REVISION: typ.Final = "29fc5a1634ffbaa18a773eed9dff1b2838a45d9c"
_MAKEUTIL_TOOLCHAIN: typ.Final = "nightly-2026-05-28"
_SKYLOS_VERSION_TOKENS: typ.Final = ("4.33.2",)
_SKYLOS_CLI_TOKENS: typ.Final = (
    "$(UV_ENV)",
    "$(UV)",
    "tool",
    "run",
    "--python",
    "3.14",
    "--from",
    "skylos==$(SKYLOS_VERSION)",
    "skylos",
)
_SKYLOS_SCAN_TOKENS: typ.Final = (
    "$(SKYLOS_CLI)",
    "--config-file",
    "pyproject.toml",
)
_SKYLOS_COMMAND_PREFIX: typ.Final = ("$(SKYLOS)",)
_SKYLOS_PRODUCTION_TARGET_TOKENS: typ.Final = ("lading",)
_SKYLOS_EXCLUSION_TOKENS: typ.Final = ("tests",)
_SKYLOS_WHITELIST_LOCK_TOKENS: typ.Final = (".skylos-whitelist.lock",)
_SKYLOS_LINT_RECIPE_TOKENS: typ.Final = (
    "$(SKYLOS)",
    "$(SKYLOS_PRODUCTION_TARGETS)",
    "--exclude",
    "$(SKYLOS_EXCLUDE_FOLDERS)",
    "--category",
    "dead_code",
    "--gate",
    "--format",
    "concise",
    "--no-upload",
    "--no-provenance",
    "--no-grep-verify",
)
_SKYLOS_WHITELIST_COMMAND_PREFIX: typ.Final = (
    "flock",
    "$(SKYLOS_WHITELIST_LOCK)",
    "env",
    "$(SKYLOS_CLI)",
)
_SKYLOS_WHITELIST_RECIPE_TOKENS: typ.Final = (
    *_SKYLOS_WHITELIST_COMMAND_PREFIX,
    "whitelist",
    "$${SKYLOS_SYMBOL}",
    "--reason",
    "$${SKYLOS_REASON}",
)
_DOCUMENTED_WHITELIST_NAMES: typ.Final = frozenset[str]()
_ENTRYPOINT_NAMES: typ.Final = frozenset({
    "lading.commands.lockfile.validate_lockfile_freshness",
    "lading.commands.lockfile._is_lockfile_stale_detail",
    "lading.commands.bump_lockfiles.CargoLockfileRepository.resolve_lockfile_paths",
    "lading.commands.bump_lockfiles.CargoLockfileRepository.regenerate_lockfiles",
    "lading.commands.lockfile.CargoLockfileInspectionRepository.validate_lockfile_freshness",
})
_MAKEUTIL_INSTALL_TOKENS: typ.Final = (
    "rustup",
    "toolchain",
    "install",
    "${MAKEUTIL_TOOLCHAIN}",
    "--profile",
    "minimal",
    "RUSTFLAGS=-Zpolonius=next",
    "cargo",
    "+${MAKEUTIL_TOOLCHAIN}",
    "install",
    "--git",
    "https://github.com/leynos/makeutil",
    "--rev",
    "${MAKEUTIL_REVISION}",
    "--locked",
    "--force",
    "makeutil",
)


def _makefile_report() -> dict[str, object]:
    """Return the complete, successfully parsed Makeutil report."""
    completed = subprocess.run(  # noqa: S603 - fixed local parser command.
        _MAKEUTIL_COMMAND,
        capture_output=True,
        check=True,
        cwd=REPOSITORY_ROOT,
        text=True,
    )
    report = typ.cast("dict[str, object]", json.loads(completed.stdout))
    parse = _mapping(report.get("parse"), subject="Makeutil parse report")
    assert parse.get("status") == "complete", (
        f"Makeutil must complete the Makefile parse, got {parse!r}"
    )
    return report


def _mapping(value: object, *, subject: str) -> dict[str, object]:
    """Return a JSON object, naming the unexpected `subject` on failure."""
    assert isinstance(value, dict), f"Expected {subject} to be a JSON object."
    return typ.cast("dict[str, object]", value)


def _objects(value: object, *, subject: str) -> list[dict[str, object]]:
    """Return a JSON object list, naming the unexpected `subject` on failure."""
    assert isinstance(value, list), f"Expected {subject} to be a JSON array."
    return [_mapping(item, subject=f"{subject} item") for item in value]


def _text_sequence(value: object, *, subject: str) -> tuple[str, ...]:
    """Return a JSON string list, naming the unexpected `subject` on failure."""
    assert isinstance(value, list), f"Expected {subject} to be a JSON array."
    assert all(isinstance(item, str) for item in value), (
        f"Expected {subject} to contain only JSON strings."
    )
    return tuple(typ.cast("list[str]", value))


def _sole_variable(name: str) -> dict[str, object]:
    """Return Makeutil's sole variable fact for `name`."""
    variables = _objects(_makefile_report().get("variables"), subject="variables")
    matches = [variable for variable in variables if variable.get("name") == name]
    assert len(matches) == 1, (
        f"Expected exactly one Makefile variable named {name!r}, found {len(matches)}."
    )
    return matches[0]


def _sole_recipe_rule(target: str) -> dict[str, object]:
    """Return the only parsed rule for `target` that has recipes."""
    rules = _objects(_makefile_report().get("rules"), subject="rules")
    matches = [
        rule
        for rule in rules
        if target in _text_sequence(rule.get("targets"), subject="rule targets")
        and _objects(rule.get("recipes"), subject="rule recipes")
    ]
    assert len(matches) == 1, (
        f"Expected one recipe-bearing Makefile rule named {target!r}, found "
        f"{len(matches)}."
    )
    return matches[0]


def _variable_tokens(name: str) -> tuple[str, ...]:
    """Return shell-like tokens from Makeutil's raw variable value."""
    value = _sole_variable(name).get("raw_value")
    assert isinstance(value, str), f"Expected {name!r} to have a string value."
    return tuple(shlex.split(value))


def _recipe_tokens(target: str) -> tuple[tuple[str, ...], ...]:
    """Return shell-like tokens for every parsed recipe in `target`."""
    recipes = _objects(
        _sole_recipe_rule(target).get("recipes"), subject=f"{target} recipes"
    )
    return tuple(
        tuple(argument for argument in shlex.split(recipe_text) if argument != "\n")
        for recipe in recipes
        if isinstance(recipe_text := recipe.get("text"), str)
    )


def _workflow_job(workflow_path: str, job_name: str) -> dict[str, object]:
    """Return the named job from a repository workflow."""
    workflow = yaml.safe_load((REPOSITORY_ROOT / workflow_path).read_text())
    workflow_mapping = _mapping(workflow, subject=f"{workflow_path} workflow")
    jobs = _mapping(workflow_mapping.get("jobs"), subject=f"{workflow_path} jobs")
    return _mapping(jobs.get(job_name), subject=f"{workflow_path} job {job_name!r}")


def _sole_workflow_step(
    workflow_path: str, job_name: str, step_name: str
) -> dict[str, object]:
    """Return the sole named workflow step from the requested job."""
    job = _workflow_job(workflow_path, job_name)
    steps = _objects(
        job.get("steps"), subject=f"{workflow_path} job {job_name!r} steps"
    )
    matches = [step for step in steps if step.get("name") == step_name]
    assert len(matches) == 1, (
        f"Expected one {step_name!r} step in {workflow_path} job {job_name!r}, "
        f"found {len(matches)}."
    )
    return matches[0]


def _assert_makeutil_installation(command: object, *, contract: str) -> None:
    """Assert that `command` installs the pinned Makeutil parser."""
    assert isinstance(command, str), (
        f"{contract} must provide a Makeutil installation shell command."
    )
    assert (
        tuple(shlex.split(command.replace("\\\n", ""))) == _MAKEUTIL_INSTALL_TOKENS
    ), f"{contract} must pin the Makeutil installation command."


def test_makefile_defines_the_strict_production_skylos_gate() -> None:
    """Keep the pinned Python 3.14 scan separate from its scan-only options."""
    test_prerequisites = _text_sequence(
        _sole_recipe_rule("test").get("prerequisites"),
        subject="test target prerequisites",
    )
    assert "makeutil" in test_prerequisites, (
        "Make test must require Makeutil for the Skylos contract suite."
    )
    assert _variable_tokens("SKYLOS_VERSION") == _SKYLOS_VERSION_TOKENS, (
        "Skylos version must pin 4.33.2."
    )
    assert _variable_tokens("SKYLOS_CLI") == _SKYLOS_CLI_TOKENS, (
        "Skylos CLI must run the pinned tool through Python 3.14."
    )
    assert _variable_tokens("SKYLOS") == _SKYLOS_SCAN_TOKENS, (
        "Skylos scan macro must add only the configuration file."
    )
    assert (
        _variable_tokens("SKYLOS_PRODUCTION_TARGETS")
        == _SKYLOS_PRODUCTION_TARGET_TOKENS
    ), "Skylos production target must scan the lading package."
    assert _variable_tokens("SKYLOS_EXCLUDE_FOLDERS") == _SKYLOS_EXCLUSION_TOKENS, (
        "Skylos exclusion must omit the tests directory."
    )
    skylos_commands = [
        command
        for command in _recipe_tokens("lint")
        if command[: len(_SKYLOS_COMMAND_PREFIX)] == _SKYLOS_COMMAND_PREFIX
    ]
    assert skylos_commands == [_SKYLOS_LINT_RECIPE_TOKENS], (
        "Skylos lint recipe must be the strict production-only dead-code scan."
    )


def test_whitelist_recipe_dispatches_before_reason() -> None:
    """Keep whitelist subcommand arguments separate from scan-only options."""
    assert _variable_tokens("SKYLOS_WHITELIST_LOCK") == _SKYLOS_WHITELIST_LOCK_TOKENS, (
        "Skylos whitelist writes must use the repository-local lock."
    )
    whitelist_commands = [
        command
        for command in _recipe_tokens("skylos-allow")
        if command[: len(_SKYLOS_WHITELIST_COMMAND_PREFIX)]
        == _SKYLOS_WHITELIST_COMMAND_PREFIX
    ]
    assert whitelist_commands == [_SKYLOS_WHITELIST_RECIPE_TOKENS], (
        "Skylos whitelist must dispatch before its symbol and --reason arguments."
    )


def test_skylos_configuration_is_strict_and_reasoned() -> None:
    """Keep every Skylos false-positive exception precise and documented."""
    configuration = tomllib.loads(
        (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    tool = _mapping(configuration.get("tool"), subject="tool configuration")
    skylos = _mapping(tool.get("skylos"), subject="Skylos configuration")
    gate = _mapping(skylos.get("gate"), subject="Skylos gate configuration")
    assert gate.get("strict") is True, "Skylos strict gate mode must remain enabled."
    whitelist = _mapping(
        skylos.get("whitelist", {}), subject="Skylos whitelist configuration"
    )
    documented = _mapping(
        whitelist.get("documented", {}), subject="Skylos documented whitelist"
    )
    assert frozenset(documented) == _DOCUMENTED_WHITELIST_NAMES, (
        "Skylos documented whitelist names changed; verify each exception and update "
        "the explicit contract set."
    )
    for symbol, reason in documented.items():
        assert isinstance(symbol, str), (
            "Every documented Skylos whitelist symbol must be a string."
        )
        assert symbol.strip(), (
            "Every documented Skylos whitelist symbol must be non-whitespace."
        )
        assert isinstance(reason, str), (
            "Every documented Skylos whitelist reason must be a string."
        )
        assert reason.strip(), (
            "Every documented Skylos whitelist reason must be non-whitespace."
        )
    dead_code = _mapping(
        skylos.get("dead_code"), subject="Skylos dead-code configuration"
    )
    entrypoints = _objects(dead_code.get("entrypoints"), subject="Skylos entrypoints")
    assert entrypoints, "Skylos dead-code configuration must retain entrypoints."
    entrypoint_names: set[str] = set()
    for entrypoint in entrypoints:
        assert entrypoint.get("type") in {"function", "method"}, (
            "Every Skylos entrypoint must name a supported symbol type."
        )
        full_names = _text_sequence(
            entrypoint.get("full_name"), subject="Skylos entrypoint full_name"
        )
        assert full_names, "Every Skylos entrypoint must name at least one symbol."
        for full_name in full_names:
            assert full_name.strip(), (
                "Every Skylos entrypoint symbol must be a non-whitespace string."
            )
            entrypoint_names.add(full_name)
        reason = entrypoint.get("reason")
        assert isinstance(reason, str), "Every Skylos entrypoint must record a reason."
        assert reason.strip(), "Every Skylos entrypoint reason must be non-whitespace."
    assert frozenset(entrypoint_names) == _ENTRYPOINT_NAMES, (
        "Skylos entrypoint names changed; verify each implicit caller and update "
        "the explicit contract set."
    )


def test_full_suite_workflows_provision_pinned_makeutil() -> None:
    """Every workflow that runs the full suite must install Makeutil itself."""
    lint_step = _sole_workflow_step(
        ".github/workflows/ci.yml",
        "lint-test",
        "Run linters, including Skylos dead-code detection",
    )
    assert lint_step.get("run") == "make lint", (
        "CI lint step must invoke the shared make lint target."
    )
    for workflow_path, job_name in (
        (".github/workflows/ci.yml", "lint-test"),
        (".github/workflows/coverage-main.yml", "coverage-upload"),
    ):
        job = _workflow_job(workflow_path, job_name)
        environment = _mapping(
            job.get("env"), subject=f"{workflow_path} {job_name} environment"
        )
        assert environment.get("MAKEUTIL_REVISION") == _MAKEUTIL_REVISION, (
            f"{workflow_path} {job_name} must pin the Makeutil revision."
        )
        assert environment.get("MAKEUTIL_TOOLCHAIN") == _MAKEUTIL_TOOLCHAIN, (
            f"{workflow_path} {job_name} must pin the Makeutil nightly toolchain."
        )
        parser_step = _sole_workflow_step(
            workflow_path, job_name, "Install Makefile parser"
        )
        _assert_makeutil_installation(
            parser_step.get("run"),
            contract=f"{workflow_path} {job_name} Makeutil-install contract",
        )
