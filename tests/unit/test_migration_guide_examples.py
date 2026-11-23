"""Contract tests for the marked examples in the 0.4.0 migration guide.

The guide names the release it migrates to, and its install commands must pin
that release. Marking every fence and executing each shell example keeps the
published commands honest without making the test depend on the current
project version, which advances with later releases.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

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


def _guide_release() -> str:
    """Return the release version named by the guide's title."""
    match = _TITLE.search(_GUIDE.read_text(encoding="utf-8"))
    assert match is not None, "the guide title must name the release it covers"
    return match.group("version")


def _write_stub(path: Path, log: Path) -> None:
    """Write an executable stub that records its argv and exits zero."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$0 $*" >> "{log}"\nexit 0\n',
        encoding="utf-8",
    )
    path.chmod(0o755)


def _run_example(example: MarkedExample, tmp_path: Path) -> str:
    """Run one shell example in an isolated PATH and return its call log."""
    bash = shutil.which("bash")
    assert bash is not None, "the example runner requires bash"
    bin_dir = tmp_path / "bin"
    log = tmp_path / "calls.log"
    for name in ("uv", "python", "python3.14"):
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
    return log.read_text(encoding="utf-8")


def test_every_marked_example_has_content() -> None:
    """The loader rejects unmarked fences and every marked body is non-empty."""
    examples = load_tested_examples(_GUIDE)

    assert examples, "the migration guide must carry marked examples"
    for example in examples.values():
        assert example.body.strip(), (
            f"tested-example {example.identifier!r} must have a body"
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

        match = re.search(r"lading==(\d+\.\d+\.\d+(?:[a-zA-Z]+\d+)?)", example.body)
        assert match is not None, f"{identifier!r} must pin an exact lading version"
        pinned = match.group(1)
        # Match the complete release components, not a character prefix:
        # 0.4.01rc1 starts with "0.4.0" but its patch component is not 0.
        prerelease = rf"{re.escape(release)}[a-zA-Z]+\d+"
        assert re.fullmatch(prerelease, pinned), (
            f"{identifier!r} must pin a pre-release of {release}, not {pinned}"
        )


@pytest.mark.parametrize("identifier", _INSTALL_EXAMPLES)
def test_marked_examples_execute_in_an_isolated_boundary(
    identifier: str, tmp_path: Path
) -> None:
    """Each marked shell example runs and reaches its package manager."""
    example = load_tested_examples(_GUIDE)[identifier]

    log = _run_example(example, tmp_path)

    assert "lading" in log, (
        f"{identifier!r} must invoke a package manager with a lading requirement"
    )
