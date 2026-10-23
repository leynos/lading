"""Execution and report-validation contracts for the nose detector."""

import copy
import re
import typing as typ
from pathlib import Path

import pytest
from duplication_gate_test_support import (
    STUB_REPORT,
    detector,
    stub_runner,
    stub_settings,
)

if typ.TYPE_CHECKING:
    from collections import abc as cabc


class TestRunDetector:
    """Report parsing and finding normalization."""

    def test_default_runner_uses_the_supplied_repository_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The process boundary receives its working directory explicitly."""
        observed: dict[str, object] = {}

        def run(
            command: list[str], **kwargs: object
        ) -> detector.subprocess.CompletedProcess[str]:
            observed["working_directory"] = kwargs["cwd"]
            return detector.subprocess.CompletedProcess(
                args=command, returncode=0, stdout="nose 0.20.0\n", stderr=""
            )

        monkeypatch.setattr(detector.subprocess, "run", run)
        assert detector._default_runner(tmp_path)(["nose", "--version"]) == (
            "nose 0.20.0\n"
        ), "The default runner returns the detector's standard output."
        assert observed["working_directory"] == tmp_path, (
            "Detector execution uses the repository root supplied by the caller."
        )

    def test_normalizes_a_stub_report(self) -> None:
        """A stub report becomes one ordered finding with both locations."""
        findings = detector.run_detector(
            stub_settings(),
            environment=detector.NoseEnvironment(
                repository_root=detector.REPO_ROOT,
                binary_override="/stub/nose",
                search_path=None,
            ),
            binary_finder=detector.discover_binary,
            runner=stub_runner(),
        )
        assert len(findings) == 1, "The stub report contains one family."
        assert findings[0].label == "lading/a.py:1-20 ~ lading/b.py:30-49", (
            "Findings must report both spans."
        )

    @pytest.mark.parametrize(
        ("families", "expected_values", "expected_labels"),
        [
            pytest.param(
                [
                    {
                        "witness": "copy-paste",
                        "value": 5.0,
                        "locations": [
                            {
                                "file": "lading/z.py",
                                "start": 1,
                                "end": 2,
                                "name": None,
                            },
                            {
                                "file": "lading/y.py",
                                "start": 1,
                                "end": 2,
                                "name": None,
                            },
                        ],
                    },
                    {
                        "witness": "exact",
                        "value": 9.0,
                        "locations": [
                            {
                                "file": "lading/a.py",
                                "start": 1,
                                "end": 2,
                                "name": "run",
                            },
                            {
                                "file": "lading/b.py",
                                "start": 1,
                                "end": 2,
                                "name": "run",
                            },
                        ],
                    },
                ],
                [9.0, 5.0],
                [
                    "lading/a.py:1-2 run ~ lading/b.py:1-2 run",
                    "lading/z.py:1-2 ~ lading/y.py:1-2",
                ],
                id="descending-value",
            ),
            pytest.param(
                [
                    {
                        "witness": "copy-paste",
                        "value": 5.0,
                        "locations": [
                            {
                                "file": "lading/z.py",
                                "start": 1,
                                "end": 2,
                                "name": None,
                            },
                            {
                                "file": "lading/y.py",
                                "start": 1,
                                "end": 2,
                                "name": None,
                            },
                        ],
                    },
                    {
                        "witness": "copy-paste",
                        "value": 5.0,
                        "locations": [
                            {
                                "file": "lading/b.py",
                                "start": 1,
                                "end": 2,
                                "name": None,
                            },
                            {
                                "file": "lading/a.py",
                                "start": 1,
                                "end": 2,
                                "name": None,
                            },
                        ],
                    },
                ],
                [5.0, 5.0],
                [
                    "lading/b.py:1-2 ~ lading/a.py:1-2",
                    "lading/z.py:1-2 ~ lading/y.py:1-2",
                ],
                id="location-label-tie-break",
            ),
        ],
    )
    def test_orders_findings(
        self,
        families: list[dict[str, object]],
        expected_values: list[float],
        expected_labels: list[str],
    ) -> None:
        """Findings sort by descending value, then by normalized location label."""
        findings = detector.normalize_findings({"families": families})

        assert [finding.value for finding in findings] == expected_values, (
            "Higher-value families must sort first."
        )
        assert [finding.label for finding in findings] == expected_labels, (
            "Equal values must order by normalized location label."
        )

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            pytest.param(7, 7.0, id="integer"),
            pytest.param(7.5, 7.5, id="float"),
        ],
    )
    def test_normalizes_numeric_values_to_float(
        self,
        value: float,
        expected: float,
    ) -> None:
        """Integer and floating-point family values both normalize to float."""
        report = copy.deepcopy(STUB_REPORT)
        typ.cast("dict[str, typ.Any]", report)["families"][0]["value"] = value
        findings = detector.normalize_findings(report)
        assert isinstance(findings[0].value, float), (
            "Normalization must coerce family values to float."
        )
        assert findings[0].value == expected, "Normalization must preserve the value."

    @pytest.mark.parametrize(
        ("mutate", "diagnostic"),
        [
            pytest.param(
                lambda report: report.__setitem__("families", {}),
                "families must be an array",
                id="families-object",
            ),
            pytest.param(
                lambda report: report["families"][0].__setitem__("value", "high"),
                "value must be a number",
                id="string-value",
            ),
            pytest.param(
                lambda report: report["families"][0].update({"value": True}),
                "value must be a number",
                id="boolean-value",
            ),
            pytest.param(
                lambda report: report["families"][0].pop("value"),
                "value must be a number",
                id="missing-value",
            ),
            pytest.param(
                lambda report: report["families"][0].__setitem__(
                    "locations", "lading/a.py"
                ),
                "locations must be an array",
                id="string-locations",
            ),
            pytest.param(
                lambda report: report["families"][0].__setitem__(
                    "locations", b"lading/a.py"
                ),
                "locations must be an array",
                id="bytes-locations",
            ),
            pytest.param(
                lambda report: report["families"][0].__setitem__("locations", []),
                "locations must not be empty",
                id="empty-locations",
            ),
            pytest.param(
                lambda report: report["families"][0]["locations"].__setitem__(
                    0, "lading/a.py"
                ),
                "families[0].locations[0] must be a table",
                id="malformed-first-location",
            ),
            pytest.param(
                lambda report: report["families"][0]["locations"][0].__setitem__(
                    "start", 0
                ),
                "start must be a positive integer",
                id="zero-start",
            ),
            pytest.param(
                lambda report: report["families"][0]["locations"][0].__setitem__(
                    "end", 0
                ),
                "end must not precede start",
                id="inverted-span",
            ),
            pytest.param(
                lambda report: report["families"][0]["locations"][0].__setitem__(
                    "name", ""
                ),
                "name must be a non-empty string or null",
                id="empty-name",
            ),
        ],
    )
    def test_rejects_malformed_reports(
        self,
        mutate: cabc.Callable[[dict[str, typ.Any]], None],
        diagnostic: str,
    ) -> None:
        """Schema violations fail at the detector boundary."""
        report = copy.deepcopy(STUB_REPORT)
        mutate(typ.cast("dict[str, typ.Any]", report))
        with pytest.raises(detector.GateConfigError, match=re.escape(diagnostic)):
            detector.normalize_findings(report)

    def test_rejects_unreadable_output(self) -> None:
        """Non-JSON detector output fails with an execution error."""

        def runner(command: cabc.Sequence[str]) -> str:
            return "nose 0.20.0\n" if "--version" in command else "not json"

        with pytest.raises(detector.GateExecutionError, match="not valid JSON"):
            detector.run_detector(
                stub_settings(),
                environment=detector.NoseEnvironment(
                    repository_root=detector.REPO_ROOT,
                    binary_override="/stub/nose",
                    search_path=None,
                ),
                binary_finder=detector.discover_binary,
                runner=runner,
            )

    def test_run_command_reports_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A slow detector becomes an actionable execution error."""

        def timeout(*_args: object, **_kwargs: object) -> typ.NoReturn:
            raise detector.subprocess.TimeoutExpired(["nose", "query"], 120)

        monkeypatch.setattr(detector.subprocess, "run", timeout)

        with pytest.raises(
            detector.GateExecutionError, match="timed out after 120 seconds"
        ):
            detector._run_command(
                ("nose", "query"), working_directory=detector.REPO_ROOT
            )

    def test_run_command_reports_a_non_zero_exit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A detector that fails carries its status and diagnostic out."""

        def failing(
            *_args: object, **_kwargs: object
        ) -> detector.subprocess.CompletedProcess[str]:
            return detector.subprocess.CompletedProcess(
                args=["nose", "query"], returncode=2, stdout="", stderr="bad query\n"
            )

        monkeypatch.setattr(detector.subprocess, "run", failing)

        with pytest.raises(
            detector.GateExecutionError,
            match=r"nose exited with status 2: bad query",
        ):
            detector._run_command(
                ("nose", "query"), working_directory=detector.REPO_ROOT
            )

    @pytest.mark.parametrize(
        ("error", "expected_message"),
        [
            pytest.param(
                PermissionError(13, "Permission denied"),
                r"cannot run nose: .*Permission denied.*make install-nose",
                id="permission-denied",
            ),
            pytest.param(
                FileNotFoundError(2, "No such file or directory"),
                r"cannot run nose: .*No such file or directory.*make install-nose",
                id="missing-executable",
            ),
        ],
    )
    def test_run_command_reports_an_execution_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        error: OSError,
        expected_message: str,
    ) -> None:
        """An unrunnable detector points at the install remediation."""

        def unrunnable(*_args: object, **_kwargs: object) -> typ.NoReturn:
            raise error

        monkeypatch.setattr(detector.subprocess, "run", unrunnable)

        with pytest.raises(detector.GateExecutionError, match=expected_message):
            detector._run_command(
                ("nose", "query"), working_directory=detector.REPO_ROOT
            )
