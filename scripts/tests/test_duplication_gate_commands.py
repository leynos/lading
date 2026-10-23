"""Command-line behaviour tests for the duplication gate.

These drive the CLI through injected readers and detectors, so they exercise
the orchestration, diagnostics, and exit statuses without the pinned binary.
``test_duplication_gate_e2e.py`` covers the same commands against the real
detector.
"""

import os
import tomllib
import typing as typ
from collections import abc as cabc
from pathlib import Path

import pytest
from duplication_gate_test_support import (
    allowlist,
    detector,
    gate,
    stub_settings,
    write_stub_nose,
)

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion

#: The stale-entry diagnostic, kept as a literal so a change to the honesty
#: wording has to be made deliberately in both places.
_STALE_LINE = (
    "stale allow entry (lading/gone.py): unmatched in this scan "
    "(verify the source before removing it; the ranked view is capped, so "
    "absence here is not proof the duplication is gone)\n"
)


def _raise(error: Exception) -> typ.Never:
    """Raise the supplied failure from an injected test boundary."""
    raise error


def _finding() -> detector.Finding:
    """Build a representative blocking finding."""
    return detector.Finding(
        witness="copy-paste",
        value=22.1,
        locations=(
            detector.Location(file="lading/a.py", start=1, end=20, name=None),
            detector.Location(file="lading/b.py", start=30, end=49, name="beta"),
        ),
    )


class TestGateCommands:
    """CLI orchestration: exit statuses, diagnostics, and failure translation."""

    def test_check_reports_blocking_findings(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        snapshot: SnapshotAssertion,
    ) -> None:
        """The check command emits the blocking report and status one."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(gate, "load_allowlist", lambda _path: ())
        monkeypatch.setattr(gate, "detect_findings", lambda: [_finding()])
        with pytest.raises(SystemExit) as error:
            gate.check()
        assert error.value.code == 1, "Blocking findings must return status one."
        assert snapshot == capsys.readouterr().out, (
            "The blocking report must remain actionable and deterministic."
        )

    def test_check_reports_stale_entries(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Allow entries covering nothing are reported for review."""
        monkeypatch.chdir(tmp_path)
        entry = allowlist.AllowEntry(keys=("lading/gone.py",), reason="resolved")
        monkeypatch.setattr(gate, "load_allowlist", lambda _path: (entry,))
        monkeypatch.setattr(gate, "detect_findings", lambda: [])
        gate.check()
        assert capsys.readouterr().out == (_STALE_LINE + "duplication gate passed\n"), (
            "Stale entries must be reported alongside a passing gate."
        )

    @pytest.mark.parametrize(
        ("allowlist_reader", "finding_detector", "error_message"),
        [
            pytest.param(
                lambda _path: _raise(OSError("unreadable configuration")),
                lambda: [],
                "unreadable configuration",
                id="allowlist-io",
            ),
            pytest.param(
                lambda _path: (),
                lambda: _raise(OSError("detector executable unavailable")),
                "detector executable unavailable",
                id="detector-io",
            ),
        ],
    )
    def test_check_inputs_wrap_environment_failures(
        self,
        allowlist_reader: cabc.Callable[[object], tuple[allowlist.AllowEntry, ...]],
        finding_detector: cabc.Callable[[], list[detector.Finding]],
        error_message: str,
    ) -> None:
        """Injected reader and detector failures become explicit gate errors."""
        with pytest.raises(gate.GateExecutionError, match=error_message):
            gate._check_inputs(
                allowlist_reader=allowlist_reader,
                detector=finding_detector,
            )

    def test_read_allowlist_returns_the_reader_result(self) -> None:
        """A successful reader result reaches the caller unchanged."""
        entry = allowlist.AllowEntry(keys=("lading/a.py",), reason="reviewed")

        def reader(_path: object) -> tuple[allowlist.AllowEntry, ...]:
            return (entry,)

        assert gate._read_allowlist(reader) == (entry,), (
            "The reader's entries must pass through unchanged."
        )

    def test_detect_findings_returns_the_detector_result(self) -> None:
        """A successful detector result reaches the caller unchanged."""
        finding = detector.Finding(
            witness="copy-paste",
            value=9.0,
            locations=(
                detector.Location(file="lading/a.py", start=1, end=2, name=None),
                detector.Location(file="lading/b.py", start=1, end=2, name=None),
            ),
        )

        def detect() -> list[detector.Finding]:
            return [finding]

        assert gate._detect_findings(detect) == [finding], (
            "The detector's findings must pass through unchanged."
        )

    @pytest.mark.parametrize("path_value", [None, "", os.pathsep.join(("one", "two"))])
    def test_detect_findings_supplies_ordered_search_directories(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        path_value: str | None,
    ) -> None:
        """The CLI adapts PATH into explicit directories after local nose."""
        pyproject_path = tmp_path / "pyproject.toml"
        monkeypatch.setattr(gate, "PYPROJECT", pyproject_path)
        if path_value is None:
            monkeypatch.delenv("PATH", raising=False)
        else:
            monkeypatch.setenv("PATH", path_value)
        monkeypatch.delenv("NOSE_BIN", raising=False)
        monkeypatch.setattr(gate, "load_settings", lambda _path: stub_settings())
        observed: dict[str, object] = {}

        def run_detector(
            _settings: detector.NoseSettings,
            *,
            environment: detector.NoseEnvironment,
            binary_finder: detector.BinaryFinder,
            runner: detector.CommandRunner,
        ) -> list[detector.Finding]:
            observed["environment"] = environment
            observed["binary_finder"] = binary_finder
            observed["runner"] = runner
            return []

        monkeypatch.setattr(gate, "run_detector", run_detector)
        assert gate.detect_findings() == [], "The detector result must pass through."

        environment = typ.cast("detector.NoseEnvironment", observed["environment"])
        configured_directories = (
            () if not path_value else tuple(path_value.split(os.pathsep))
        )
        assert environment.search_directories == (
            str(tmp_path / detector.DEFAULT_NOSE_RELATIVE_PATH.parent),
            *configured_directories,
        ), "Explicit PATH entries must follow the repository-local binary."
        assert observed["binary_finder"] is detector.discover_binary, (
            "The CLI supplies the repository-local binary finder."
        )
        assert callable(observed["runner"]), "The CLI supplies detector execution."

    @pytest.mark.parametrize(
        ("invoke", "error", "expected_type", "expected_message"),
        [
            pytest.param(
                lambda error: gate._read_allowlist(
                    lambda _path: _raise(error),
                ),
                OSError("unreadable configuration"),
                gate.GateExecutionError,
                "cannot load duplication allowlist: unreadable configuration",
                id="allowlist-os-error",
            ),
            pytest.param(
                lambda error: gate._read_allowlist(
                    lambda _path: _raise(error),
                ),
                tomllib.TOMLDecodeError("bad table", "", 0),
                gate.GateExecutionError,
                "cannot load duplication allowlist: bad table (at end of document)",
                id="allowlist-toml-error",
            ),
            pytest.param(
                lambda error: gate._detect_findings(lambda: _raise(error)),
                OSError("detector executable unavailable"),
                gate.GateExecutionError,
                "nose detector failed: detector executable unavailable",
                id="detector-os-error",
            ),
            pytest.param(
                lambda error: gate._detect_findings(lambda: _raise(error)),
                TypeError("families must be an array"),
                gate.GateConfigError,
                "families must be an array",
                id="detector-type-error",
            ),
            pytest.param(
                lambda error: gate._detect_findings(lambda: _raise(error)),
                ValueError("value must be a number"),
                gate.GateConfigError,
                "value must be a number",
                id="detector-value-error",
            ),
        ],
    )
    def test_gate_boundaries_translate_expected_failures(
        self,
        invoke: cabc.Callable[[Exception], object],
        error: Exception,
        expected_type: type[Exception],
        expected_message: str,
    ) -> None:
        """Injected boundary failures retain their type-specific diagnostics."""
        with pytest.raises(expected_type) as raised:
            invoke(error)

        assert str(raised.value) == expected_message, "Diagnostic must name the cause."
        assert raised.value.__cause__ is error, "The original error must be the cause."

    def test_detect_findings_does_not_wrap_runtime_errors(self) -> None:
        """Programming faults from a detector are not reclassified as I/O failures."""
        error = RuntimeError("detector runtime failed")

        def detect() -> list[detector.Finding]:
            raise error

        with pytest.raises(RuntimeError) as raised:
            gate._detect_findings(detect)

        assert raised.value is error, "Runtime errors must propagate unchanged."

    def test_read_allowlist_passes_configuration_errors_through(self) -> None:
        """An allowlist configuration error is not rewrapped."""
        error = gate.GateConfigError("duplication_gate.allow[0] requires a reason")

        def reader(_path: object) -> tuple[allowlist.AllowEntry, ...]:
            raise error

        with pytest.raises(gate.GateConfigError) as raised:
            gate._read_allowlist(reader)

        assert raised.value is error, "The original configuration error must propagate."

    def test_detect_findings_passes_configuration_errors_through(self) -> None:
        """A detector configuration error is not rewrapped."""
        error = gate.GateConfigError("nose 0.19.0 is installed but 0.20.0 is pinned")

        def detect() -> list[detector.Finding]:
            raise error

        with pytest.raises(gate.GateConfigError) as raised:
            gate._detect_findings(detect)

        assert raised.value is error, "The original configuration error must propagate."

    def test_check_reports_detector_schema_errors(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Malformed detector reports exit cleanly instead of showing a traceback."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(gate, "load_allowlist", lambda _path: ())

        def raise_schema_error() -> list[detector.Finding]:
            msg = "nose report families must be an array"
            raise TypeError(msg)

        monkeypatch.setattr(gate, "detect_findings", raise_schema_error)
        with pytest.raises(SystemExit) as error:
            gate.check()

        assert error.value.code == 2, "Malformed detector reports must return two."
        assert capsys.readouterr().err == (
            "configuration error: nose report families must be an array\n"
        ), "Schema errors must use the configuration diagnostic."
        assert Path.cwd() == tmp_path, (
            "The check command must not change its caller's working directory."
        )

    def test_check_reports_a_version_mismatch(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """An unpinned detector fails the gate with a remediation message."""
        workspace = tmp_path
        stub = write_stub_nose(workspace, version="nose 0.19.0")
        monkeypatch.setenv("NOSE_BIN", str(stub))
        monkeypatch.chdir(workspace)
        monkeypatch.setattr(gate, "load_allowlist", lambda _path: ())
        with pytest.raises(SystemExit) as error:
            gate.check()
        assert error.value.code == 2, "A version mismatch must return two."
        assert "make install-nose" in capsys.readouterr().err, (
            "The mismatch diagnostic must name the install remediation."
        )

    def test_allow_reports_malformed_existing_entries(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Malformed existing allows exit cleanly instead of showing a traceback."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[[tool.duplication_gate.allow]]\nunit = "lading/a.py"\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(gate, "PYPROJECT", pyproject)

        with pytest.raises(SystemExit) as error:
            gate.allow(first="lading/b.py", reason="reviewed exception")

        assert error.value.code == 2, "Malformed existing allows must return two."
        assert capsys.readouterr().err == (
            "configuration error: duplication_gate.allow[0] "
            "requires a non-empty reason\n"
        ), "Malformed allows must use the configuration diagnostic."

    def test_allow_reports_write_failures(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A filesystem failure while recording an allow exits cleanly."""
        write_error = OSError("read-only filesystem")

        def fail_write(*_args: object, **_kwargs: object) -> None:
            raise write_error

        monkeypatch.setattr(gate, "append_allow_entry", fail_write)

        with pytest.raises(SystemExit) as exit_error:
            gate.allow(first="lading/a.py", reason="reviewed exception")

        assert exit_error.value.code == 2, "Write failures must return two."
        assert capsys.readouterr().err == (
            "configuration error: read-only filesystem\n"
        ), "Write failures must use the configuration diagnostic."

    def test_allow_rejects_malformed_keys(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """An absolute key is refused before anything is written."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[project]\nname = 'x'\n", encoding="utf-8")
        monkeypatch.setattr(gate, "PYPROJECT", pyproject)

        with pytest.raises(SystemExit) as error:
            gate.allow(first="/lading/a.py", reason="reviewed exception")

        assert error.value.code == 2, "Malformed keys must return two."
        assert "repository-relative" in capsys.readouterr().err, (
            "The diagnostic must explain the key requirement."
        )
        assert "duplication_gate" not in pyproject.read_text(encoding="utf-8"), (
            "A rejected key must not be recorded."
        )

    def test_allow_rejects_an_empty_reason(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A blank reason is refused before anything is written."""
        with pytest.raises(SystemExit) as error:
            gate.allow(first="lading/a.py", reason="   ")

        assert error.value.code == 2, "An empty reason must return two."
        assert capsys.readouterr().err == "REASON must not be empty\n", (
            "The diagnostic must name the missing reason."
        )
