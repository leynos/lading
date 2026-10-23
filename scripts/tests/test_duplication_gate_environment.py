"""Test environment adaptation at the duplication gate's CLI boundary."""

import os
import typing as typ
from pathlib import Path

import pytest
from duplication_gate_test_support import detector, gate, stub_settings


class TestGateEnvironmentAdapter:
    """CLI adaptation of process environment into detector inputs."""

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
