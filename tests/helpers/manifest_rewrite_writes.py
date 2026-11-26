"""Instrument atomic manifest writes in recovery tests."""

import pathlib
import typing as typ

from lading.commands import (
    bump_manifest_rewrite_application,
    bump_pipeline_manifest_restore,
    bump_toml_files,
)

if typ.TYPE_CHECKING:
    import pytest

    from lading.commands.bump_toml_write_options import AtomicWriteOptions


def record_workspace_write_attempts(
    monkeypatch: pytest.MonkeyPatch,
    root_path: pathlib.Path,
) -> list[pathlib.Path]:
    """Record atomic writer destinations within one workspace."""
    write_attempts: list[pathlib.Path] = []
    original_writer = bump_toml_files.write_atomic_text

    def record_write(
        path: pathlib.Path,
        content: str,
        write_options: AtomicWriteOptions | None = None,
    ) -> None:
        """Record a workspace write and preserve the atomic writer behaviour."""
        if path.is_relative_to(root_path):
            write_attempts.append(path)
        original_writer(path, content, write_options)

    for module in (
        bump_toml_files,
        bump_manifest_rewrite_application,
        bump_pipeline_manifest_restore,
    ):
        monkeypatch.setattr(module, "write_atomic_text", record_write)
    return write_attempts
