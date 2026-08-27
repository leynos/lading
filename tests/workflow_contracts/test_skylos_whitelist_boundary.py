"""Exercise the Skylos named-whitelist boundary without changing its policy file."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tomllib
from contextlib import ExitStack
from pathlib import Path

import hypothesis as hyp
import hypothesis.strategies as st
from hypothesis import HealthCheck

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_SHELL_ARGUMENT_TEXT = st.builds(
    lambda prefix, content, suffix: prefix + content + suffix,
    st.text(alphabet=" \t", max_size=4),
    st.text(
        alphabet="abcdefghijklmnopqrstuvwxyz0123456789_$;|&'\"()[]{}*?!\\`",
        min_size=1,
        max_size=40,
    ),
    st.text(alphabet=" \t", max_size=4),
)


def _make_executable() -> str:
    """Return the absolute path to the required Make executable."""
    executable = shutil.which("make")
    assert executable is not None, "Skylos whitelist boundary tests require make."
    return executable


def _run_skylos_allow(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Run an invalid whitelist boundary with WSL's NAME value injected."""
    environment = {**os.environ, "NAME": "wsl-hostname"}
    environment.pop("REASON", None)
    environment.pop("SYMBOL", None)
    environment.update(argument.split("=", maxsplit=1) for argument in arguments)
    return subprocess.run(  # noqa: S603 - fixed Make target and arguments.
        (_make_executable(), "--no-print-directory", "skylos-allow"),
        capture_output=True,
        check=False,
        cwd=REPOSITORY_ROOT,
        env=environment,
        text=True,
    )


def _whitelist_command(directory: Path, *, cli: str) -> tuple[str, ...]:
    """Build an isolated documented-whitelist command."""
    return (
        _make_executable(),
        "-f",
        str(REPOSITORY_ROOT / "Makefile"),
        f"SKYLOS_CLI={cli}",
        f"SKYLOS_WHITELIST_LOCK={directory / '.skylos-whitelist.lock'}",
        "skylos-allow",
    )


@hyp.settings(max_examples=25, deadline=None)
@hyp.given(value=st.text(alphabet=" \t", min_size=1, max_size=8))
def test_skylos_allow_rejects_missing_or_whitespace_values(value: str) -> None:
    """Reject absent input and input that contains whitespace only."""
    requests = (
        ((), "SYMBOL"),
        (("SYMBOL=handler",), "REASON"),
        ((f"SYMBOL={value}", "REASON=reason"), "SYMBOL"),
        (("SYMBOL=handler", f"REASON={value}"), "REASON"),
    )
    for arguments, missing_name in requests:
        completed = _run_skylos_allow(*arguments)
        assert completed.returncode == 2, (
            f"Skylos whitelist must reject missing {missing_name} with exit 2."
        )
        assert (
            f"Error: {missing_name} is required for a named whitelist exception"
            in completed.stderr
        ), f"Skylos whitelist must identify missing {missing_name}."


@hyp.settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@hyp.example(symbol=" $(handler);* ", reason=' Loaded "$plugin" | registry ')
@hyp.given(symbol=_SHELL_ARGUMENT_TEXT, reason=_SHELL_ARGUMENT_TEXT)
def test_skylos_allow_forwards_generated_argument_boundaries(
    tmp_path: Path, symbol: str, reason: str
) -> None:
    """Pass every valid generated value to Skylos as exactly one argument."""
    recorded_arguments = tmp_path / "arguments.json"
    recorder = tmp_path / "skylos-recorder"
    recorder.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "import sys\n"
        "from pathlib import Path\n\n"
        "Path(os.environ['SKYLOS_ARGUMENTS_PATH']).write_text(\n"
        "    json.dumps(sys.argv[1:]), encoding='utf-8'\n"
        ")\n",
        encoding="utf-8",
    )
    recorder.chmod(0o755)
    configuration_path = REPOSITORY_ROOT / "pyproject.toml"
    before = configuration_path.read_bytes()
    completed = subprocess.run(  # noqa: S603 - fixed Make target and recorder.
        (
            _make_executable(),
            "--no-print-directory",
            f"SKYLOS_CLI={recorder}",
            f"SKYLOS_WHITELIST_LOCK={tmp_path / '.skylos-whitelist.lock'}",
            "skylos-allow",
        ),
        capture_output=True,
        check=False,
        cwd=REPOSITORY_ROOT,
        env={
            **os.environ,
            "NAME": "wsl-hostname",
            "REASON": reason,
            "SKYLOS_ARGUMENTS_PATH": str(recorded_arguments),
            "SYMBOL": symbol,
        },
        text=True,
    )
    assert completed.returncode == 0, (
        f"Recorder-backed forwarding failed: {completed.stderr}"
    )
    assert json.loads(recorded_arguments.read_text(encoding="utf-8")) == [
        "whitelist",
        symbol,
        "--reason",
        reason,
    ], "Skylos must receive each generated value as exactly one ordered argument."
    assert configuration_path.read_bytes() == before, (
        "Recorder-backed whitelist forwarding must not modify pyproject.toml."
    )


def test_skylos_whitelist_lock_preserves_concurrent_entries(tmp_path: Path) -> None:
    """Serialize documented whitelist updates so neither entry is lost."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.skylos.whitelist.documented]\n", encoding="utf-8"
    )
    writer = tmp_path / "write_whitelist_entry.py"
    writer.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "import time\n"
        "symbol = sys.argv[2]\n"
        "reason = sys.argv[4]\n"
        "path = Path('pyproject.toml')\n"
        "contents = path.read_text(encoding='utf-8')\n"
        "time.sleep(0.2)\n"
        "path.write_text(contents + f'{symbol} = {reason!r}\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    cli = f"{sys.executable} {writer}"
    commands = (
        _whitelist_command(tmp_path, cli=cli),
        _whitelist_command(tmp_path, cli=cli),
    )
    with ExitStack() as processes:
        requests = tuple(
            processes.enter_context(
                subprocess.Popen(  # noqa: S603 - fixed Makefile and test arguments.
                    command,
                    cwd=tmp_path,
                    env={
                        **os.environ,
                        "REASON": reason,
                        "SYMBOL": symbol,
                    },
                    stderr=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    text=True,
                )
            )
            for command, symbol, reason in zip(
                commands,
                ("first", "second"),
                ("first reason", "second reason"),
                strict=True,
            )
        )
        results = tuple(request.communicate() for request in requests)
        for request, (stdout, stderr) in zip(requests, results, strict=True):
            assert request.returncode == 0, (
                "Concurrent documented whitelist updates must succeed: "
                f"{stdout}{stderr}"
            )
    with (tmp_path / "pyproject.toml").open("rb") as configuration_file:
        configuration = tomllib.load(configuration_file)
    documented = configuration["tool"]["skylos"]["whitelist"]["documented"]
    assert documented == {"first": "first reason", "second": "second reason"}, (
        "Skylos whitelist lock must preserve every concurrent documented entry."
    )
