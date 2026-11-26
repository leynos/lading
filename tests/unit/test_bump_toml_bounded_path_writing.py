"""Exercise descriptor-relative atomic writes for bounded TOML files."""

import os
from pathlib import Path

import pytest

from lading.commands import (
    bump_toml_bounded_filesystem,
    bump_toml_files,
    bump_toml_write_options,
)
from lading.exceptions import LadingError


def test_bounded_target_rejects_an_escaping_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A destination resolver failure becomes a permission error."""
    destination = tmp_path / "Cargo.toml"

    def reject_resolution(*_args: object, **_kwargs: object) -> Path:
        """Reject the containment check for the planned destination."""
        message = "outside the workspace"
        raise LadingError(message)

    monkeypatch.setattr(
        bump_toml_files.manifest_paths, "resolve_workspace_path", reject_resolution
    )

    with pytest.raises(PermissionError, match="must stay within the workspace"):
        bump_toml_files._resolve_bounded_write_target(destination, tmp_path)


def test_bounded_target_rejects_a_changed_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A destination that resolves elsewhere is refused as a changed target."""
    destination = tmp_path / "Cargo.toml"
    other = tmp_path / "other.toml"

    monkeypatch.setattr(
        bump_toml_files.manifest_paths,
        "resolve_workspace_path",
        lambda *_args, **_kwargs: other,
    )

    with pytest.raises(PermissionError, match="no longer resolves to its planned path"):
        bump_toml_files._resolve_bounded_write_target(destination, tmp_path)


@pytest.mark.parametrize(
    ("path_parts", "message"),
    [
        pytest.param(None, "Manifest must stay within the workspace", id="outside"),
        pytest.param(
            ("sub", "..", "Cargo.toml"),
            "Manifest must stay within the workspace",
            id="parent-segment",
        ),
    ],
)
def test_bounded_read_rejects_escaping_paths(
    tmp_path: Path,
    path_parts: tuple[str, ...] | None,
    message: str,
) -> None:
    """A bounded read rejects escaping paths and lexical parent traversal."""
    if not bump_toml_files._supports_bounded_directory_operations():
        pytest.skip("descriptor-relative replacement is unavailable on this platform")
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    if path_parts is None:
        candidate = tmp_path / "outside.toml"
        candidate.write_text("outside", encoding="utf-8")
    else:
        (workspace_root / "sub").mkdir()
        candidate = workspace_root.joinpath(*path_parts)

    with pytest.raises(PermissionError, match=message):
        bump_toml_bounded_filesystem._read_bounded_regular_text(
            candidate, workspace_root
        )


def test_bounded_writer_uses_short_temporary_name_for_long_destination(
    tmp_path: Path,
) -> None:
    """Descriptor-relative temporary names also ignore the destination name."""
    if not bump_toml_files._supports_bounded_directory_operations():
        pytest.skip("descriptor-relative replacement is unavailable on this platform")
    destination = tmp_path / ("m" * 245)
    destination.write_text("original", encoding="utf-8")

    bump_toml_files.write_atomic_text(
        destination,
        "replacement",
        bump_toml_write_options.AtomicWriteOptions(workspace_root=tmp_path),
    )

    assert destination.read_text(encoding="utf-8") == "replacement", (
        "bounded replacement must not exceed the filesystem's basename limit"
    )
    assert not list(tmp_path.glob(".lading-*")), (
        "bounded replacement must leave no temporary file"
    )


def test_bounded_writer_accepts_workspace_relative_destination(
    tmp_path: Path,
) -> None:
    """A relative destination resolves from the workspace root."""
    if not bump_toml_files._supports_bounded_directory_operations():
        pytest.skip("descriptor-relative replacement is unavailable on this platform")
    destination = tmp_path / "Cargo.toml"
    destination.write_text("original", encoding="utf-8")

    bump_toml_files.write_atomic_text(
        Path("Cargo.toml"),
        "replacement",
        bump_toml_write_options.AtomicWriteOptions(workspace_root=tmp_path),
    )

    assert destination.read_text(encoding="utf-8") == "replacement", (
        "bounded writes must accept workspace-relative destinations"
    )


def test_bounded_writer_keeps_write_error_when_temporary_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Descriptor-relative cleanup errors do not replace the write failure."""
    if not bump_toml_files._supports_bounded_directory_operations():
        pytest.skip("descriptor-relative replacement is unavailable on this platform")
    destination = tmp_path / "Cargo.toml"
    destination.write_text("original", encoding="utf-8")
    write_error_message = "mode update rejected"

    def reject_mode_update(_descriptor: int, _mode: int) -> None:
        """Fail after temporary creation so descriptor cleanup is exercised."""
        raise OSError(write_error_message)

    monkeypatch.setattr(bump_toml_files, "_set_file_mode", reject_mode_update)
    original_unlink = os.unlink
    cleanup_error_message = "cleanup denied"

    def reject_temporary_unlink(
        path: str | Path,
        *,
        dir_fd: int | None = None,
    ) -> None:
        """Reject cleanup only for the writer's uniquely named temporary."""
        if Path(path).name.startswith(".lading-"):
            raise PermissionError(cleanup_error_message)
        if dir_fd is None:
            original_unlink(path)
        else:
            original_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", reject_temporary_unlink)
    monkeypatch.setattr(
        bump_toml_files, "_supports_bounded_directory_operations", lambda: True
    )

    with pytest.raises(OSError, match=write_error_message) as failure:
        bump_toml_files.write_atomic_text(
            destination,
            "replacement",
            bump_toml_write_options.AtomicWriteOptions(workspace_root=tmp_path),
        )

    assert any(cleanup_error_message in note for note in failure.value.__notes__), (
        "cleanup failure details should be attached to the original write error"
    )
    assert destination.read_text(encoding="utf-8") == "original", (
        "the failed bounded replacement must leave destination bytes unchanged"
    )
    temporary_paths = tuple(tmp_path.glob(".lading-*"))
    assert len(temporary_paths) == 1, "the injected cleanup failure should be visible"
    original_unlink(temporary_paths[0])
