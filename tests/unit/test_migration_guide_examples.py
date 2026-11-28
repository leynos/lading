"""Contract tests for the marked examples in the 0.4.0 migration guide.

The guide names the release it migrates to, and its install commands must pin
that release. Marking every fence and executing each shell example keeps the
published commands honest without making the test depend on the current
project version, which advances with later releases.
"""

import dataclasses as dc
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from lading.commands import bump_docs_fences
from tests.helpers.tested_examples import MarkedExample, load_tested_examples

_GUIDE = Path("docs/v0-4-0-migration-guide.md")
_TITLE = re.compile(
    r"^# Migration guide: v(?P<previous>\d+\.\d+\.\d+) to "
    r"v(?P<version>\d+\.\d+\.\d+)$",
    re.MULTILINE,
)
_PRERELEASE_EXAMPLES = (
    "migration-upgrade-uv-prerelease",
    "migration-upgrade-pip-prerelease",
)
_INSTALL_EXAMPLES = (*_PRERELEASE_EXAMPLES, "migration-defer-upgrade")


@dc.dataclass(frozen=True, slots=True)
class _Invocation:
    """One recorded process invocation with its argument boundaries."""

    program: str
    arguments: tuple[str, ...]


_UV_INTERPRETER_STEPS = (
    _Invocation(program="uv", arguments=("python", "install", "3.14")),
    _Invocation(program="uv", arguments=("python", "pin", "3.14")),
)


def _expected_uv_prerelease(pinned: str) -> tuple[_Invocation, ...]:
    """Return the uv steps that install the pinned pre-release."""
    return (
        *_UV_INTERPRETER_STEPS,
        _Invocation(program="uv", arguments=("add", pinned)),
    )


def _expected_pip_prerelease(pinned: str) -> tuple[_Invocation, ...]:
    """Return the pip steps that create the environment and install the pin."""
    return (
        _Invocation(program="python3.14", arguments=("-m", "venv", ".venv")),
        _Invocation(
            program=".venv/bin/python",
            arguments=("-m", "pip", "install", pinned),
        ),
    )


def _expected_defer(release: str) -> tuple[_Invocation, ...]:
    """Return the single uv step that bounds the deferred release."""
    return (_Invocation(program="uv", arguments=("add", f"lading<{release}")),)


def _guide_release() -> str:
    """Return the release version named by the guide's title."""
    match = _TITLE.search(_GUIDE.read_text(encoding="utf-8"))
    assert match is not None, "the guide title must name the release it covers"
    return match.group("version")


def _pinned_version(example: MarkedExample, release: str) -> str:
    """Return the pre-release version ``example`` pins."""
    match = re.search(r"lading==(\d+\.\d+\.\d+(?:[a-zA-Z]+\d+)?)", example.body)
    assert match is not None, f"{example.identifier!r} must pin an exact lading version"
    pinned = match.group(1)
    prerelease = rf"{re.escape(release)}[a-zA-Z]+\d+"
    assert re.fullmatch(prerelease, pinned), (
        f"{example.identifier!r} must pin a pre-release of {release}, not {pinned}"
    )
    return pinned


def _write_stub(path: Path, log: Path) -> None:
    """Write an executable stub that records its argv boundaries."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\n"
        'printf \'CMD %s\\n\' "$0" >> "$CALL_LOG"\n'
        'for arg in "$@"; do\n'
        '  printf \'ARG %s\\n\' "$arg" >> "$CALL_LOG"\n'
        "done\n"
        "printf 'END\\n' >> \"$CALL_LOG\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _read_invocations(log: Path) -> tuple[_Invocation, ...]:
    """Return the invocations recorded in ``log``."""
    invocations: list[_Invocation] = []
    program: str | None = None
    arguments: list[str] = []
    for line in log.read_text(encoding="utf-8").splitlines():
        if line.startswith("CMD "):
            program = line.removeprefix("CMD ")
            arguments = []
        elif line.startswith("ARG "):
            arguments.append(line.removeprefix("ARG "))
        elif line == "END" and program is not None:
            invocations.append(
                _Invocation(
                    program=_stable_program(program),
                    arguments=tuple(arguments),
                )
            )
            program = None
    return tuple(invocations)


def _stable_program(program: str) -> str:
    """Return a stable label, dropping an absolute PATH-resolved directory."""
    path = Path(program)
    return path.name if path.is_absolute() else program


def _run_example(example: MarkedExample, tmp_path: Path) -> tuple[_Invocation, ...]:
    """Run one shell example in an isolated PATH and return its invocations."""
    bash = shutil.which("bash")
    assert bash is not None, "the example runner requires bash"
    bin_dir = tmp_path / "bin"
    log = tmp_path / "calls.log"
    for name in ("uv", "python", "python3.14", "lading"):
        _write_stub(bin_dir / name, log)
    _write_stub(tmp_path / ".venv" / "bin" / "python", log)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "CALL_LOG": str(log),
    }
    subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv
        [bash, "-euo", "pipefail", "-c", example.body],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return _read_invocations(log)


def test_every_marked_example_has_content() -> None:
    """The loader rejects unmarked fences and every marked body is non-empty."""
    examples = load_tested_examples(_GUIDE)

    assert examples, "the migration guide must carry marked examples"
    for example in examples.values():
        assert example.body.strip(), (
            f"tested-example {example.identifier!r} must have a body"
        )


def test_documentation_snippet_examples_match_the_documentation_pass() -> None:
    """The guide's snippet examples behave as the documentation pass does."""
    examples = load_tested_examples(_GUIDE)
    bumped = {"alpha"}
    target = "1.2.3"

    unrelated = examples["migration-docs-unrelated-package"]
    unrelated_updated, unrelated_changed = (
        bump_docs_fences.update_toml_snippet_versions(unrelated.body, bumped, target)
    )
    assert not unrelated_changed, (
        "the guide claims an unrelated package keeps its version"
    )
    assert unrelated_updated == unrelated.body, (
        "the unrelated-package example must survive the pass unchanged"
    )

    eligible = examples["migration-docs-eligible-package"]
    eligible_updated, eligible_changed = bump_docs_fences.update_toml_snippet_versions(
        eligible.body, bumped, target
    )
    assert eligible_changed, "the guide claims a bumped crate's version moves"
    assert f'version = "{target}"' in eligible_updated, (
        "the eligible example must be rewritten to the guide's target version"
    )


def test_prerelease_examples_pin_the_guide_release() -> None:
    """The marked install commands pin a pre-release of the guide's release."""
    examples = load_tested_examples(_GUIDE)
    release = _guide_release()

    for identifier in _INSTALL_EXAMPLES:
        example = examples.get(identifier)
        assert example is not None, (
            f"the migration guide must mark the {identifier!r} example"
        )
        assert example.language == "bash", f"{identifier!r} must be a shell example"

        if identifier == "migration-defer-upgrade":
            assert f"lading<{release}" in example.body, (
                f"{identifier!r} must bound the deferred release below {release}"
            )
            continue

        _pinned_version(example, release)


@pytest.mark.parametrize("identifier", _INSTALL_EXAMPLES)
def test_marked_examples_execute_in_an_isolated_boundary(
    identifier: str, tmp_path: Path
) -> None:
    """Each marked shell example runs its documented commands unchanged."""
    examples = load_tested_examples(_GUIDE)
    example = examples[identifier]
    release = _guide_release()
    invocations = _run_example(example, tmp_path)

    if identifier == "migration-defer-upgrade":
        assert invocations == _expected_defer(release), (
            "the defer example must only add the bounded requirement"
        )
        return

    pinned = f"lading=={_pinned_version(example, release)}"
    if identifier == "migration-upgrade-uv-prerelease":
        assert invocations == _expected_uv_prerelease(pinned), (
            "each uv command must match the documented interpreter and package steps"
        )
        return

    assert invocations == _expected_pip_prerelease(pinned), (
        "the pip example must create the environment then install the pre-release"
    )


def test_bump_diff_example_executes(tmp_path: Path) -> None:
    """The documented bump diff command runs unchanged in the isolated boundary."""
    example = load_tested_examples(_GUIDE)["migration-bump-diff"]

    assert example.language == "bash", "the example must be a shell fence"
    invocations = _run_example(example, tmp_path)

    assert invocations == (
        _Invocation(
            program="lading",
            arguments=("bump", "1.2.3", "--dry-run", "--diff"),
        ),
    ), "the example must invoke the documented bump diff command"
