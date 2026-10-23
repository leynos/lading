"""Tests for the pinned nose detector wrapper used by the duplication gate."""

import dataclasses as dc
import re
import textwrap
import typing as typ

import pytest
from duplication_gate_test_support import (
    detector,
    stub_runner,
    stub_settings,
    write_stub_nose,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

    from syrupy.assertion import SnapshotAssertion


def _settings_body(
    *,
    version: str | None = '"0.20.0"',
    roots: str = '["lading"]',
    min_size: str = "24",
    surface: str | None = None,
) -> str:
    """Build a `[tool.nose]` table body from the supplied literal values."""
    lines = ["[tool.nose]"]
    if version is not None:
        lines.append(f"version = {version}")
    lines.extend((f"roots = {roots}", 'mode = "syntax"', f"min-size = {min_size}"))
    if surface is not None:
        lines.append(f"surface = {surface}")
    return "\n".join(lines) + "\n"


class TestLoadSettings:
    """`[tool.nose]` settings parsing."""

    def _write(self, tmp_path: Path, body: str) -> Path:
        """Write ``body`` to ``pyproject.toml`` under ``tmp_path``."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(textwrap.dedent(body), encoding="utf-8")
        return pyproject

    def test_loads_the_repository_settings(self, tmp_path: Path) -> None:
        """A complete table produces validated settings."""
        pyproject = self._write(
            tmp_path,
            """\
            [tool.nose]
            version = "0.20.0"
            roots = ["lading"]
            mode = "syntax"
            min-size = 24
            surface = "all"
            top = 30
            exclude = ["**/generated/**"]
            """,
        )
        settings = detector.load_settings(pyproject)
        assert settings.roots == ("lading",), "Roots must round-trip in order."
        assert settings.exclude == ("**/generated/**",), (
            "Exclude globs must round-trip."
        )
        assert settings.top == 30, "The ranking bound must round-trip."

    def test_top_and_exclude_are_optional(self, tmp_path: Path) -> None:
        """Omitted optional keys fall back to nose's own view size."""
        pyproject = self._write(
            tmp_path,
            """\
            [tool.nose]
            version = "0.20.0"
            roots = ["lading"]
            mode = "syntax"
            min-size = 24
            """,
        )
        settings = detector.load_settings(pyproject)
        assert settings.top is None, "An omitted `top` must not bound the view."
        assert settings.surface == "all", "The gate defaults to the widened surface."

    @pytest.mark.parametrize(
        ("body", "diagnostic"),
        [
            (
                _settings_body(version=None),
                "tool.nose.version must be a non-empty string",
            ),
            (
                _settings_body(roots='"lading"'),
                "tool.nose.roots must be an array of strings",
            ),
            (
                _settings_body(min_size="0"),
                "tool.nose.min-size must be a positive integer",
            ),
            (
                _settings_body(surface='"everything"'),
                "tool.nose.surface must be 'default' or 'all'",
            ),
        ],
        ids=["missing-version", "string-roots", "zero-min-size", "bad-surface"],
    )
    def test_rejects_malformed_settings(
        self, tmp_path: Path, body: str, diagnostic: str
    ) -> None:
        """Malformed settings raise a configuration error."""
        pyproject = self._write(tmp_path, body)
        with pytest.raises(detector.GateConfigError, match=re.escape(diagnostic)):
            detector.load_settings(pyproject)


class TestResolveBinary:
    """Discovery and version verification of the pinned binary."""

    def test_accepts_the_pinned_version(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A binary reporting the pinned version is accepted."""
        stub = write_stub_nose(tmp_path)
        monkeypatch.setenv("NOSE_BIN", str(stub))
        assert detector.resolve_binary(stub_settings(), runner=stub_runner()) == str(
            stub
        ), "The pinned binary must be returned unchanged."

    def test_resolves_a_relative_override_against_the_repository_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A relative NOSE_BIN is resolved from the repository, not the caller."""
        repo_stub = detector.REPO_ROOT / "lading"
        assert repo_stub.is_dir(), "The repository root must contain the package."
        monkeypatch.setenv("NOSE_BIN", "lading")
        monkeypatch.chdir(tmp_path)

        assert detector.resolve_binary(stub_settings(), runner=stub_runner()) == str(
            repo_stub.resolve()
        ), "A relative override must resolve against the repository root."

    def test_rejects_a_version_mismatch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A different installed version fails with a remediation hint."""
        stub = write_stub_nose(tmp_path, version="nose 0.19.0")
        monkeypatch.setenv("NOSE_BIN", str(stub))
        with pytest.raises(
            detector.GateExecutionError,
            match=r"reports 'nose 0\.19\.0'.*make install-nose",
        ):
            detector.resolve_binary(
                stub_settings(), runner=stub_runner(version="nose 0.19.0")
            )

    def test_reports_a_missing_binary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A missing detector fails with the install remediation."""
        monkeypatch.delenv("NOSE_BIN", raising=False)
        monkeypatch.setattr(detector, "_discover_binary", lambda: None)
        with pytest.raises(detector.GateExecutionError, match="make install-nose"):
            detector.resolve_binary(stub_settings(), runner=stub_runner())


class TestBuildCommand:
    """Translation of gate settings into a nose query command."""

    def test_pins_every_configured_setting(self, snapshot: SnapshotAssertion) -> None:
        """The whole argument vector is pinned, in order, from the settings."""
        settings = dc.replace(
            stub_settings(),
            roots=("lading", "scripts"),
            mode="semantic",
            min_size=40,
            surface="all",
            top=30,
            exclude=("**/generated/**", "**/_vendor/**"),
        )

        command = detector.build_command("nose", settings)

        assert command == snapshot, (
            "Every configured setting must reach nose, in the documented order."
        )

    def test_default_surface_omits_the_all_term(
        self, snapshot: SnapshotAssertion
    ) -> None:
        """The default surface leaves nose on its ranked dashboard."""
        settings = dc.replace(
            stub_settings(),
            roots=("lading",),
            mode="syntax",
            min_size=24,
            surface="default",
            top=None,
            exclude=(),
        )

        command = detector.build_command("nose", settings)

        assert command == snapshot, (
            "The default surface must not widen the view or pass a ranking bound."
        )

    def test_never_asks_nose_to_report_every_family(self) -> None:
        """A bounded ranking view must stay bounded."""
        settings = dc.replace(stub_settings(), top=1)

        command = detector.build_command("nose", settings)

        assert "top=0" not in command, (
            "top=0 would disable the ranking bound rather than widen the view."
        )
