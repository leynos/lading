"""End-to-end contracts for the duplication gate CLI and Make boundary."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from duplication_gate_test_support import (
    REPOSITORY_ROOT,
    allowlist,
    copied_gate_workspace,
    detector,
    gate_environment,
    run_gate_command,
    write_stub_nose,
)


class TestGateWorkflowContracts:
    """The real CLI and Make boundaries, exercised end to end."""

    @pytest.mark.parametrize(
        ("second", "expected_keys"),
        [
            pytest.param(None, ("lading/a.py",), id="unit"),
            pytest.param(
                ["lading/b.py::beta"],
                ("lading/a.py", "lading/b.py::beta"),
                id="members",
            ),
        ],
    )
    def test_allow_cli_round_trips_unit_and_members(
        self,
        tmp_path: Path,
        second: list[str] | None,
        expected_keys: tuple[str, ...],
    ) -> None:
        """The real allow CLI records both supported exception forms."""
        _workspace, script = copied_gate_workspace(tmp_path)
        arguments = ["allow", "--first", "lading/a.py"]
        for key in second or ():
            arguments.extend(("--second", key))
        arguments.extend(("--reason", "reviewed exception"))

        result = run_gate_command(script, *arguments)
        assert result.returncode == 0, result.stderr
        entries = allowlist.load_allowlist(script.parent.parent / "pyproject.toml")
        assert entries[0].keys == expected_keys, "CLI must retain its requested keys."
        assert entries[0].reason == "reviewed exception", (
            "CLI must retain the supplied reason."
        )

    @pytest.mark.parametrize(
        ("additional_arguments", "expected_keys"),
        [
            pytest.param(
                (
                    "--second",
                    "lading/b.py::beta",
                    "--second",
                    "lading/c.py::gamma",
                ),
                ("lading/a.py", "lading/b.py::beta", "lading/c.py::gamma"),
                id="repeated-second-options",
            ),
            pytest.param(
                (
                    "--second-keys-json",
                    '["lading/b.py::beta", "lading/c.py::gamma"]',
                ),
                ("lading/a.py", "lading/b.py::beta", "lading/c.py::gamma"),
                id="json-array-option",
            ),
        ],
    )
    def test_allow_cli_records_larger_families(
        self,
        tmp_path: Path,
        additional_arguments: tuple[str, ...],
        expected_keys: tuple[str, ...],
    ) -> None:
        """Each supported member-key form records the complete family."""
        _workspace, script = copied_gate_workspace(tmp_path)
        result = run_gate_command(
            script,
            "allow",
            "--first",
            "lading/a.py",
            *additional_arguments,
            "--reason",
            "three parallel contracts",
        )

        assert result.returncode == 0, result.stderr
        entries = allowlist.load_allowlist(script.parent.parent / "pyproject.toml")
        assert entries[0].keys == expected_keys, (
            "Every supplied member key must be recorded in order."
        )

    @pytest.mark.parametrize(
        ("json_value", "expected_diagnostic"),
        [
            pytest.param("not-json", "JSON array of strings", id="invalid-json"),
            pytest.param(
                '{"key": "lading/b.py"}', "JSON array of strings", id="object"
            ),
            pytest.param(
                '["lading/b.py", 2]', "JSON array of strings", id="non-string-key"
            ),
        ],
    )
    def test_allow_cli_rejects_malformed_json_key_arrays(
        self,
        tmp_path: Path,
        json_value: str,
        expected_diagnostic: str,
    ) -> None:
        """Malformed JSON member inputs return a configuration error."""
        _workspace, script = copied_gate_workspace(tmp_path)
        result = run_gate_command(
            script,
            "allow",
            "--first",
            "lading/a.py",
            "--second-keys-json",
            json_value,
            "--reason",
            "reviewed exception",
        )

        assert result.returncode == 2, result.stderr
        assert expected_diagnostic in result.stderr, (
            "Invalid JSON member keys must have an actionable diagnostic."
        )

    def test_allow_cli_rejects_both_member_key_options(
        self,
        tmp_path: Path,
    ) -> None:
        """The CLI refuses ambiguous repeated and JSON member-key inputs."""
        _workspace, script = copied_gate_workspace(tmp_path)
        result = run_gate_command(
            script,
            "allow",
            "--first",
            "lading/a.py",
            "--second",
            "lading/b.py",
            "--second-keys-json",
            '["lading/c.py"]',
            "--reason",
            "ambiguous inputs",
        )

        assert result.returncode == 2, result.stderr
        assert "SECOND and SECOND_KEYS_JSON cannot be supplied together" in (
            result.stderr
        ), "The CLI must identify the conflicting member-key options."

    def test_allow_cli_rejects_malformed_allowlist_with_status_two(
        self,
        tmp_path: Path,
    ) -> None:
        """Malformed existing allow configuration is a handled CLI error."""
        workspace, script = copied_gate_workspace(tmp_path)
        pyproject = workspace / "pyproject.toml"
        original = "[tool.duplication_gate]\nallow = false\n"
        pyproject.write_text(original, encoding="utf-8")

        result = run_gate_command(
            script,
            "allow",
            "--first",
            "lading/a.py",
            "--reason",
            "reviewed exception",
        )

        assert result.returncode == 2, result.stderr
        assert "duplication_gate.allow must be an array" in result.stderr, (
            "Malformed allow configuration must have a clear diagnostic."
        )
        assert pyproject.read_text(encoding="utf-8") == original, (
            "A rejected allow command must preserve the source document."
        )

    def test_check_cli_passes_with_a_stub_detector(self, tmp_path: Path) -> None:
        """The gate exits zero through its real CLI when every family is allowed."""
        workspace, script = copied_gate_workspace(tmp_path)
        stub = write_stub_nose(tmp_path)
        (workspace / "pyproject.toml").write_text(
            textwrap.dedent(
                """\
                [tool.nose]
                version = "0.20.0"
                roots = ["lading"]
                mode = "syntax"
                min-size = 24

                [[tool.duplication_gate.allow]]
                members = ["lading/a.py", "lading/b.py"]
                reason = "parallel wire contracts"
                """
            ),
            encoding="utf-8",
        )
        result = run_gate_command(
            script,
            "check",
            environment=gate_environment(NOSE_BIN=str(stub)),
        )
        assert result.returncode == 0, result.stderr
        assert "duplication gate passed" in result.stdout, (
            "An allowed family must leave the gate passing."
        )

    def test_check_cli_fails_closed_when_the_binary_is_absent(
        self,
        tmp_path: Path,
    ) -> None:
        """A missing detector is an execution failure, never a clean result."""
        workspace, script = copied_gate_workspace(tmp_path)
        (workspace / "pyproject.toml").write_text(
            textwrap.dedent(
                """\
                [tool.nose]
                version = "0.20.0"
                roots = ["lading"]
                mode = "syntax"
                min-size = 24
                """
            ),
            encoding="utf-8",
        )
        result = run_gate_command(
            script,
            "check",
            environment=gate_environment(NOSE_BIN=str(tmp_path / "absent-nose")),
        )
        assert result.returncode == 2, (
            f"A missing detector must fail closed.\n{result.stdout}{result.stderr}"
        )
        assert "duplication gate passed" not in result.stdout, (
            "A missing detector must never report a passing gate."
        )

    def test_real_check_cli_passes(self) -> None:
        """The checked-in gate runs successfully through its real CLI boundary."""
        settings = detector.load_settings(REPOSITORY_ROOT / "pyproject.toml")
        try:
            detector.resolve_binary(
                settings,
                environment=detector.NoseEnvironment(
                    repository_root=REPOSITORY_ROOT,
                    binary_override=os.environ.get("NOSE_BIN"),
                    search_path=os.environ.get("PATH"),
                ),
                binary_finder=detector.discover_binary,
            )
        except detector.GateExecutionError as error:  # pragma: no cover
            pytest.skip(str(error))
        result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed repository gate command.
            [
                sys.executable,
                str(REPOSITORY_ROOT / "scripts" / "duplication_gate.py"),
                "check",
            ],
            cwd=REPOSITORY_ROOT,
            env=gate_environment(),
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "duplication gate passed" in result.stdout, (
            "Real check invocation must report its successful gate result."
        )
