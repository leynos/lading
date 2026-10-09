"""Unit tests for the snapshot path-normalization helpers.

The helpers exist to keep a run-dependent temporary directory, and the
platform's concrete ``Path`` class, out of a recorded snapshot. A snapshot
that names ``PosixPath`` or ``WindowsPath`` accepts correct calls on one
platform and rejects them on the other, so the portable rendering is a
contract in its own right rather than an incidental detail of the tests that
use it. These tests pin that contract directly, at the boundary where it is
implemented.
"""

import dataclasses as dc
import pathlib

from tests.helpers.path_normalization import (
    TMP_PLACEHOLDER,
    normalized,
    normalized_argv,
    normalized_captured,
    normalized_invocations,
)


def test_normalized_replaces_the_path_text() -> None:
    """The temporary directory is replaced wherever it appears."""
    text = f"wrote {TMP_PLACEHOLDER}/Cargo.lock"

    assert normalized(text, pathlib.Path(TMP_PLACEHOLDER)) == (
        "wrote <tmp>/Cargo.lock"
    ), "the helper must substitute the path's text for the placeholder"


def test_normalized_accepts_a_literal_prefix() -> None:
    """A Markdown link prefix is normalized as text, not coerced to a path.

    ``str(pathlib.Path("../../"))`` drops the trailing slash and renders with
    backslashes on Windows; Markdown syntax uses forward slashes. A caller
    holding a link prefix must therefore be able to pass the text it means,
    and the helper must use it verbatim.
    """
    markdown = "See [Guide](../../docs/guide.md)."

    assert normalized(markdown, "../../", placeholder="<link-prefix>") == (
        "See [Guide](<link-prefix>docs/guide.md)."
    ), "the prefix must be replaced as written, trailing slash included"


def test_normalized_argv_replaces_text_in_every_element() -> None:
    """Each command word is normalized without disturbing the list shape."""
    argv = ["cargo", "update", f"--manifest-path={TMP_PLACEHOLDER}/Cargo.toml"]

    assert normalized_argv(argv, pathlib.Path(TMP_PLACEHOLDER)) == [
        "cargo",
        "update",
        "--manifest-path=<tmp>/Cargo.toml",
    ], "every argv element must be normalized and the ordering preserved"


def test_normalized_captured_renders_paths_without_a_class_name() -> None:
    """A captured ``Path`` must not put its concrete class in the snapshot.

    This is the defect the helper exists for: rendering the mapping with
    ``repr`` produces ``PosixPath('<tmp>')``, which a run on Windows spells
    ``WindowsPath``. The value must render as path text instead.
    """
    captured = {"workspace_root": pathlib.Path(TMP_PLACEHOLDER)}

    rendered = normalized_captured(captured.items(), pathlib.Path(TMP_PLACEHOLDER))

    assert rendered == {"workspace_root": "<tmp>"}, (
        "a captured Path must render as portable path text"
    )
    assert "Path" not in rendered["workspace_root"], (
        "no concrete Path class name may survive into the snapshot"
    )


def test_normalized_captured_keeps_non_path_values_distinguishable() -> None:
    """Text, containers and scalars keep a rendering that separates them."""
    captured = {
        "manifests": ("crates/ui/Cargo.toml",),
        "runner": None,
        "calls": 1,
    }

    rendered = normalized_captured(captured.items(), pathlib.Path(TMP_PLACEHOLDER))

    assert rendered == {
        "manifests": "('crates/ui/Cargo.toml',)",
        "runner": "None",
        "calls": "1",
    }, "non-path values must stay distinguishable by their repr"


@dc.dataclass(frozen=True, slots=True)
class _Invocation:
    """A minimal stand-in for a recorded command invocation."""

    command: tuple[str, ...]
    cwd: pathlib.Path | None


def test_normalized_invocations_renders_command_and_directory() -> None:
    """An invocation becomes one deterministic line naming argv and cwd."""
    invocations = [
        _Invocation(
            command=("cargo", "update", "--manifest-path", "Cargo.toml"),
            cwd=pathlib.Path(TMP_PLACEHOLDER),
        )
    ]

    assert normalized_invocations(invocations, pathlib.Path(TMP_PLACEHOLDER)) == [
        "cargo update --manifest-path Cargo.toml (cwd=<tmp>)"
    ], "the invocation must render as its command and working directory"


def test_normalized_invocations_reports_a_missing_directory() -> None:
    """An invocation that ran nowhere renders as ``cwd=None``."""
    invocations = [_Invocation(command=("cargo", "metadata"), cwd=None)]

    assert normalized_invocations(invocations, pathlib.Path(TMP_PLACEHOLDER)) == [
        "cargo metadata (cwd=None)"
    ], "an absent working directory must be visible rather than omitted"
