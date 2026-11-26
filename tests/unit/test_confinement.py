"""Unit tests for the bump confinement primitives: patterns and the walk.

Every rejection test places a valid target beside the invalid input and asserts
the valid one is accepted, so a policy that rejected everything could not pass.
The special-file tests build a FIFO where the platform allows it and otherwise
skip, and an ``open`` sentinel proves no target is opened to classify it.
"""

import builtins
import errno
import io
import os
import pathlib
import typing as typ

import pytest

from lading.utils.confinement import (
    BumpTargetExaminationError,
    ConfineOptions,
    MissingBumpTargetError,
    ProofContext,
    TargetKind,
    UnsafeBumpTargetError,
    confine_relative_path,
    establish_workspace_root,
    prove_containment,
    validate_relative_pattern,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

pytest_plugins = ("tests.unit.confinement_fixtures",)

_ESCAPING_PATTERNS = [
    pytest.param("../outside.md", id="posix-parent"),
    pytest.param("..\\outside.md", id="windows-parent"),
    pytest.param("docs/../../outside.md", id="nested-parent"),
    pytest.param("docs/..\\outside.md", id="mixed-parent"),
    pytest.param("/etc/passwd", id="posix-absolute"),
    pytest.param("\\windows\\system32", id="windows-rooted"),
    pytest.param("C:/Windows", id="drive-qualified"),
    pytest.param("c:relative", id="drive-relative"),
    pytest.param("//server/share/file", id="unc"),
    pytest.param("\\\\server\\share\\file", id="unc-backslash"),
    pytest.param("", id="empty"),
    pytest.param(".", id="current-directory"),
    pytest.param("   ", id="whitespace"),
    pytest.param("docs/\x00/evil.md", id="nul-byte"),
]


@pytest.mark.parametrize("pattern", _ESCAPING_PATTERNS)
def test_validate_relative_pattern_rejects_unsafe_patterns(pattern: str) -> None:
    """Reject absolute, rooted, UNC, and parent-traversing patterns."""
    with pytest.raises(UnsafeBumpTargetError) as excinfo:
        validate_relative_pattern(pattern, key="workspace.members")
    assert "workspace.members" in str(excinfo.value), (
        "the diagnostic must name the configuration key that supplied the pattern"
    )
    assert validate_relative_pattern("crates/alpha", key="workspace.members") == (
        "crates",
        "alpha",
    ), "a plain relative pattern must still be accepted"


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("Cargo.toml", ("Cargo.toml",)),
        ("crates/*", ("crates", "*")),
        ("docs/**/*.md", ("docs", "**", "*.md")),
        (".hidden/file", (".hidden", "file")),
    ],
)
def test_validate_relative_pattern_accepts_relative_globs(
    pattern: str, expected: tuple[str, ...]
) -> None:
    """Accept ordinary relative paths and globs under either spelling of key."""
    assert validate_relative_pattern(pattern, key="k") == expected, (
        f"{pattern!r} must decompose into {expected!r}"
    )


def test_establish_workspace_root_rejects_a_missing_root(tmp_path: Path) -> None:
    """Report a missing workspace root as a preflight error, not an OSError."""
    missing = tmp_path / "absent"
    with pytest.raises(MissingBumpTargetError) as excinfo:
        establish_workspace_root(missing)
    assert "does not exist" in str(excinfo.value), (
        "the diagnostic must say the workspace root was not found"
    )


def test_establish_workspace_root_rejects_a_file(tmp_path: Path) -> None:
    """Require the workspace root itself to be a directory."""
    candidate = tmp_path / "root-file"
    candidate.write_text("", encoding="utf-8")
    with pytest.raises(UnsafeBumpTargetError) as excinfo:
        establish_workspace_root(candidate)
    assert "not a directory" in str(excinfo.value), (
        "a file must be reported as a non-directory workspace root"
    )


def test_establish_workspace_root_follows_a_symlinked_ancestor(
    tmp_path: Path,
) -> None:
    """Accept a workspace reached through a symlink above the root itself."""
    real_parent = tmp_path / "real"
    (real_parent / "workspace").mkdir(parents=True)
    link_parent = tmp_path / "link"
    link_parent.symlink_to(real_parent, target_is_directory=True)
    root = establish_workspace_root(link_parent / "workspace")
    assert root == (real_parent / "workspace").resolve(), (
        "a workspace reached through an ancestor symlink must resolve to the real "
        "directory"
    )


def test_confine_relative_path_accepts_a_confined_file(tmp_path: Path) -> None:
    """Accept a regular file whose every component is a real directory."""
    root = establish_workspace_root(tmp_path)
    (root / "crates" / "alpha").mkdir(parents=True)
    manifest = root / "crates" / "alpha" / "Cargo.toml"
    manifest.write_text("", encoding="utf-8")
    confined = confine_relative_path(root, "crates/alpha/Cargo.toml", key="crate")
    assert confined.path == manifest, "the confined path must equal the real file"
    assert confined.relative.as_posix() == "crates/alpha/Cargo.toml", (
        "the confined path must remember its root-relative spelling"
    )


def test_confine_relative_path_rejects_a_symlinked_directory(
    tmp_path: Path,
) -> None:
    """Reject a member directory that is a symlink to a sibling outside the root."""
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "Cargo.toml").write_text("external", encoding="utf-8")
    (root / "linked").symlink_to(outside, target_is_directory=True)
    (root / "real").mkdir()
    (root / "real" / "Cargo.toml").write_text("", encoding="utf-8")
    with pytest.raises(UnsafeBumpTargetError) as excinfo:
        confine_relative_path(root, "linked/Cargo.toml", key="members[0]")
    message = str(excinfo.value)
    assert "symlink" in message, "the diagnostic must say a symlink was rejected"
    assert "linked/Cargo.toml" in message, (
        "the diagnostic must name the rejected target"
    )
    accepted = confine_relative_path(root, "real/Cargo.toml", key="members[1]")
    assert accepted.path == root / "real" / "Cargo.toml", (
        "the valid member ordered beside the symlink must still be accepted"
    )


def test_confine_relative_path_rejects_a_final_symlink(tmp_path: Path) -> None:
    """Reject a final manifest that is a symlink to an external file."""
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    sentinel = outside / "Cargo.toml"
    sentinel.write_text("external", encoding="utf-8")
    (root / "Cargo.toml").symlink_to(sentinel)
    with pytest.raises(UnsafeBumpTargetError) as excinfo:
        confine_relative_path(root, "Cargo.toml", key="root-manifest")
    assert "symlink" in str(excinfo.value), (
        "a final symlink must be rejected before any resolution turns it external"
    )
    assert sentinel.read_text(encoding="utf-8") == "external", (
        "validation must not modify the external sentinel"
    )


def test_confine_relative_path_rejects_an_in_root_alias(tmp_path: Path) -> None:
    """Reject a symlink that points at a real in-workspace directory."""
    root = tmp_path / "workspace"
    (root / "crates" / "real").mkdir(parents=True)
    (root / "crates" / "real" / "Cargo.toml").write_text("", encoding="utf-8")
    (root / "crates" / "alias").symlink_to(
        root / "crates" / "real", target_is_directory=True
    )
    with pytest.raises(UnsafeBumpTargetError) as excinfo:
        confine_relative_path(root, "crates/alias/Cargo.toml", key="members")
    assert "symlink" in str(excinfo.value), (
        "an in-root symlink alias must be rejected as a symlink, not accepted "
        "because it happens to stay inside the root"
    )


def test_confine_relative_path_rejects_a_dangling_link(tmp_path: Path) -> None:
    """Reject a symlink whose target does not exist without probing it."""
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "dangling").symlink_to(root / "missing")
    with pytest.raises(UnsafeBumpTargetError) as excinfo:
        confine_relative_path(root, "dangling/README.md", key="docs")
    message = str(excinfo.value)
    assert "symlink" in message, "a dangling symlink must be rejected as a symlink"
    assert "dangling/README.md" in message, "the diagnostic must name the rejected path"


def test_confine_relative_path_rejects_a_non_directory_ancestor(
    tmp_path: Path,
) -> None:
    """Reject a manifest path whose ancestor is a regular file."""
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "crates").write_text("not a directory", encoding="utf-8")
    with pytest.raises(UnsafeBumpTargetError) as excinfo:
        confine_relative_path(root, "crates/alpha/Cargo.toml", key="members")
    assert "not a directory" in str(excinfo.value), (
        "a non-directory ancestor must be rejected explicitly"
    )


def test_confine_relative_path_rejects_a_directory_as_a_file(tmp_path: Path) -> None:
    """Reject a final component that is a directory where a file is required."""
    root = tmp_path / "workspace"
    (root / "docs").mkdir(parents=True)
    with pytest.raises(UnsafeBumpTargetError) as excinfo:
        confine_relative_path(root, "docs", key="docs")
    assert "must be a regular file" in str(excinfo.value), (
        "a directory in place of a file must be rejected"
    )
    accepted = confine_relative_path(
        root, "docs", key="docs", options=ConfineOptions(expect=TargetKind.DIRECTORY)
    )
    assert accepted.path == root / "docs", (
        "the same directory must be accepted when a directory is expected"
    )


def test_confine_relative_path_allows_a_missing_creatable_target(
    tmp_path: Path,
) -> None:
    """Accept an absent final component when creation is permitted."""
    root = tmp_path / "workspace"
    (root / "crates" / "alpha").mkdir(parents=True)
    confined = confine_relative_path(
        root,
        "crates/alpha/README.md",
        key="readme",
        options=ConfineOptions(may_create=True),
    )
    assert confined.path == root / "crates" / "alpha" / "README.md", (
        "a may-create destination must be accepted when its parents are real"
    )
    with pytest.raises(MissingBumpTargetError) as excinfo:
        confine_relative_path(root, "crates/alpha/README.md", key="readme")
    assert "does not exist" in str(excinfo.value), (
        "the same path must be rejected when creation is not permitted"
    )


def test_confine_relative_path_rejects_a_missing_creatable_parent(
    tmp_path: Path,
) -> None:
    """Reject a may-create target whose parent directory does not exist."""
    root = tmp_path / "workspace"
    root.mkdir()
    with pytest.raises(MissingBumpTargetError) as excinfo:
        confine_relative_path(
            root,
            "crates/alpha/README.md",
            key="readme",
            options=ConfineOptions(may_create=True),
        )
    assert "does not exist" in str(excinfo.value), (
        "a missing parent must be rejected even for a creatable final component"
    )


def test_confine_relative_path_reports_examination_failure_separately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report an unreadable target as an examination failure, not as absence."""
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "Cargo.toml").write_text("", encoding="utf-8")
    real_lstat = os.lstat
    target = root.resolve() / "Cargo.toml"

    def failing_lstat(path: str | os.PathLike[str]) -> os.stat_result:
        """Fail only for the exact target under examination."""
        if os.fspath(path) == str(target):
            raise PermissionError(errno.EACCES, "denied", str(target))
        return real_lstat(path)

    monkeypatch.setattr(os, "lstat", failing_lstat)
    with pytest.raises(BumpTargetExaminationError) as excinfo:
        confine_relative_path(root, "Cargo.toml", key="root-manifest")
    assert "cannot examine" in str(excinfo.value), (
        "a permission failure must be reported as an examination error"
    )


def test_confine_relative_path_rejects_a_fifo_without_opening_it(
    fifo_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reject a FIFO from its ``lstat`` result and never open it."""
    root = establish_workspace_root(fifo_path.parent.parent)
    opened: list[object] = []

    def forbidden_open(*args: object, **kwargs: object) -> typ.NoReturn:
        """Record and fail on any attempt to open a target."""
        opened.append(args[0] if args else kwargs.get("file"))
        message = "validation opened a target to classify it"
        raise AssertionError(message)

    monkeypatch.setattr(builtins, "open", forbidden_open)
    # ``Path.open`` and ``Path.read_text`` call ``io.open`` rather than
    # ``builtins.open``, so both entry points are guarded to keep the sentinel
    # honest about every opening route.
    monkeypatch.setattr(io, "open", forbidden_open)
    with pytest.raises(UnsafeBumpTargetError) as excinfo:
        confine_relative_path(root, "docs/named-pipe", key="bump.documentation")
    assert "must be a regular file" in str(excinfo.value), (
        "a FIFO must be rejected as a non-regular target"
    )
    assert not opened, f"no target may be opened to classify it; opened {opened!r}"


def test_confine_relative_path_rejects_a_hard_link(tmp_path: Path) -> None:
    """A hard-linked write target could rewrite an external inode."""
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    (root / "docs").mkdir(parents=True)
    outside.mkdir()
    external = outside / "guide.md"
    external.write_text("external", encoding="utf-8")
    linked = root / "docs" / "guide.md"
    try:
        os.link(external, linked)
    except OSError as exc:  # pragma: no cover - filesystem capability
        pytest.skip(f"hard links are unavailable here: {exc}")
    if os.lstat(linked).st_nlink <= 1:  # pragma: no cover - capability
        pytest.skip("the filesystem does not report a shared link count")

    with pytest.raises(UnsafeBumpTargetError) as excinfo:
        confine_relative_path(root, "docs/guide.md", key="bump.documentation")

    assert "hard-linked" in str(excinfo.value), (
        "the diagnostic must say the target is hard-linked"
    )
    assert external.read_text(encoding="utf-8") == "external", (
        "rejecting the target must not modify the external inode"
    )


def test_establish_workspace_root_reports_an_unexpandable_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expansion failure is a preflight examination error on every host."""

    def failing_expanduser(self: pathlib.Path) -> pathlib.Path:
        """Raise the failure every platform can reproduce."""
        message = "no such user"
        raise RuntimeError(message)

    monkeypatch.setattr(pathlib.Path, "expanduser", failing_expanduser)

    with pytest.raises(BumpTargetExaminationError) as excinfo:
        establish_workspace_root("~/workspace")

    assert "Cannot expand workspace root" in str(excinfo.value), (
        "the diagnostic must name the unexpandable root"
    )


def test_creatable_containment_requires_existing_parents(tmp_path: Path) -> None:
    """A creatable target may omit only its final component."""
    root = establish_workspace_root(tmp_path)
    context = ProofContext(
        key="lockfile destination",
        display="missing/Cargo.lock",
        may_create=True,
    )

    with pytest.raises(BumpTargetExaminationError):
        prove_containment(root, root / "missing" / "Cargo.lock", context)

    prove_containment(root, root / "Cargo.lock", context)
