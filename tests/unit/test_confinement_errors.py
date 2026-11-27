"""Unit tests for confinement error branches outside the happy paths."""

import errno
import os
from pathlib import Path

import pytest

from lading.utils.confinement import (
    BumpTargetExaminationError,
    ProofContext,
    UnsafeBumpTargetError,
    establish_workspace_root,
    prove_containment,
    relative_to_root,
)
from lading.utils.confinement import patterns as patterns_module
from lading.utils.confinement.walk import display_relative


def test_display_relative_falls_back_for_an_outside_path(tmp_path: Path) -> None:
    """A path outside the root is rendered absolutely."""
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"

    assert display_relative(root, outside) == str(outside), (
        "an outside path cannot be made relative to the root"
    )


def test_establish_workspace_root_reports_resolution_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A permission failure while resolving the root is an examination error."""
    real_resolve = Path.resolve

    def failing_resolve(self: Path, *, strict: bool = False) -> Path:
        """Fail only for the candidate root."""
        if self == tmp_path:
            raise PermissionError(errno.EACCES, "denied", str(self))
        return real_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", failing_resolve)

    with pytest.raises(BumpTargetExaminationError) as excinfo:
        establish_workspace_root(tmp_path)

    assert "Cannot examine workspace root" in str(excinfo.value), (
        "the diagnostic must name the root examination failure"
    )


def test_relative_to_root_rejects_an_outside_path(tmp_path: Path) -> None:
    """A candidate outside the root is rejected."""
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"

    with pytest.raises(UnsafeBumpTargetError) as excinfo:
        relative_to_root(root, outside, key="members", label="member")

    assert "outside the workspace root" in str(excinfo.value), (
        "the diagnostic must say the path escapes the root"
    )


def test_relative_to_root_reports_a_different_drive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Windows drive mismatch is reported as an unsafe target."""
    root = tmp_path / "workspace"
    root.mkdir()

    def fake_splitdrive(path: str | os.PathLike[str]) -> tuple[str, str]:
        """Give the candidate a different drive from the root."""
        drive = "D:" if Path(path).name == "Cargo.toml" else "C:"
        return drive, os.fspath(path)

    monkeypatch.setattr(os.path, "splitdrive", fake_splitdrive)

    with pytest.raises(UnsafeBumpTargetError) as excinfo:
        relative_to_root(root, root / "Cargo.toml", key="m", label="manifest")

    assert "different drive" in str(excinfo.value), (
        "the diagnostic must name the drive mismatch"
    )


def test_relative_to_root_preserves_a_parent_traversal(tmp_path: Path) -> None:
    """A parent component is rejected rather than lexically collapsed."""
    root = tmp_path.resolve() / "workspace"
    root.mkdir()
    candidate = root / "link" / ".." / "outside" / "Cargo.toml"

    with pytest.raises(UnsafeBumpTargetError) as excinfo:
        relative_to_root(root, candidate, key="m", label="manifest")

    assert "parent traversal" in str(excinfo.value), (
        "the diagnostic must name the parent-traversing component"
    )


def test_prove_containment_rejects_an_outside_candidate(tmp_path: Path) -> None:
    """The canonical proof rejects a candidate outside the root."""
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("external", encoding="utf-8")

    with pytest.raises(UnsafeBumpTargetError) as excinfo:
        prove_containment(
            root,
            outside,
            ProofContext(key="target", display="outside"),
        )

    assert "outside the workspace root" in str(excinfo.value), (
        "the canonical proof must reject the outside candidate"
    )


def test_prove_containment_resolves_an_existing_creatable_target(
    tmp_path: Path,
) -> None:
    """A creatable proof resolves an existing final component."""
    root = tmp_path / "workspace"
    root.mkdir()
    existing = root / "README.md"
    existing.write_text("content", encoding="utf-8")

    prove_containment(
        root,
        existing,
        ProofContext(key="readme", display="README.md", may_create=True),
    )


def test_native_parts_uses_windows_components_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Windows host flavour splits a backslash-separated pattern."""
    monkeypatch.setattr(patterns_module.os, "sep", "\\")

    assert patterns_module._native_parts("docs\\guide.md") == ("docs", "guide.md"), (
        "a Windows host must split the pattern into its own components"
    )


def test_prove_containment_rejects_a_dangling_final_symlink(
    tmp_path: Path,
) -> None:
    """A dangling final link is resolved strictly instead of looking absent."""
    root = tmp_path / "workspace"
    root.mkdir()
    dangling = root / "README.md"
    dangling.symlink_to(root / "missing")

    with pytest.raises(BumpTargetExaminationError) as excinfo:
        prove_containment(
            root,
            dangling,
            ProofContext(key="readme", display="README.md", may_create=True),
        )

    assert "cannot resolve target" in str(excinfo.value), (
        "the diagnostic must name the unresolvable link"
    )


def test_establish_workspace_root_reports_an_unreadable_current_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A removed current directory is a preflight examination error."""

    def failing_cwd() -> Path:
        """Raise the failure a removed working directory produces."""
        message = "current directory is gone"
        raise FileNotFoundError(message)

    monkeypatch.setattr(Path, "cwd", failing_cwd)

    with pytest.raises(BumpTargetExaminationError) as excinfo:
        establish_workspace_root(None)

    assert "Cannot expand workspace root" in str(excinfo.value), (
        "the diagnostic must name the failed current-directory lookup"
    )


def test_validate_relative_pattern_rejects_a_windows_component_with_a_drive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later component that carries its own drive is rejected on Windows."""
    monkeypatch.setattr(patterns_module.os, "sep", "\\")

    with pytest.raises(UnsafeBumpTargetError) as excinfo:
        patterns_module.validate_relative_pattern("docs/C:evil.md", key="k")

    assert "drive or root" in str(excinfo.value), (
        "the diagnostic must explain the component's own drive"
    )


def test_relative_to_root_accepts_an_alternative_spelling(
    tmp_path: Path,
) -> None:
    """An alternative spelling that names the same directory is accepted."""
    root = tmp_path.resolve()
    (root / "Cargo.toml").write_text("", encoding="utf-8")
    doubled = Path(f"{root.as_posix()}//Cargo.toml")

    relative = relative_to_root(root, doubled, key="manifest", label="manifest")

    assert relative == Path("Cargo.toml"), (
        "a redundant separator must not change the validated suffix"
    )


def test_relative_to_root_rejects_a_symlinked_component(tmp_path: Path) -> None:
    """A lexically contained path through a symlink is not beneath the root."""
    root = tmp_path.resolve() / "workspace"
    root.mkdir()
    outside = tmp_path.resolve() / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    candidate = root / "link" / "Cargo.toml"
    candidate.write_text("", encoding="utf-8")

    with pytest.raises(UnsafeBumpTargetError):
        relative_to_root(root, candidate, key="manifest", label="manifest")


def test_relative_to_root_rejects_a_case_only_sibling(tmp_path: Path) -> None:
    """A sibling that differs only in case is not beneath the root."""
    root = tmp_path.resolve() / "workspace"
    root.mkdir()
    sibling = tmp_path.resolve() / "WORKSPACE"
    sibling.mkdir()
    candidate = sibling / "Cargo.toml"
    candidate.write_text("", encoding="utf-8")

    with pytest.raises(UnsafeBumpTargetError):
        relative_to_root(root, candidate, key="manifest", label="manifest")
