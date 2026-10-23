"""Scope and exclusion contracts for nose duplication scans."""

import tomllib
import typing as typ
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _nose_table() -> dict[str, object]:
    """Read the configured detector table for scope assertions."""
    pyproject = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    tool = pyproject["tool"]
    assert isinstance(tool, dict), "The pyproject must expose a [tool] table."
    nose = tool["nose"]
    assert isinstance(nose, dict), "[tool.nose] must be a TOML table."
    return typ.cast("dict[str, object]", nose)


def test_configured_exclusions_actually_exclude_something() -> None:
    """Every exclusion glob must be able to match, at any depth.

    nose anchors ``--exclude`` globs to each ``--root`` rather than to the
    repository root. A repository-relative glob such as ``scripts/tests/**``
    therefore matches nothing when the root is ``scripts`` and silently
    excludes no files, so the gate reports families from a tree the
    configuration claims to have skipped. A glob that does not begin with
    ``**/`` only matches at the top level of a root, which is exactly the
    quietly-inert form.
    """
    nose = _nose_table()
    excludes = nose.get("exclude", [])
    assert isinstance(excludes, list), "[tool.nose].exclude must be a TOML array."

    for glob in excludes:
        assert isinstance(glob, str), "Every exclusion glob must be a string."
        assert glob.startswith("**/"), (
            f"Exclude glob {glob!r} is anchored to the top of a scan root and "
            "would exclude nothing below it; prefix it with `**/` to match at "
            "any depth under every configured root."
        )


def test_declared_exclusions_target_directories_that_exist() -> None:
    """A mistyped exclusion must not pass for a scoped scan."""
    nose = _nose_table()
    excludes = nose.get("exclude", [])
    assert isinstance(excludes, list), "[tool.nose].exclude must be a TOML array."
    roots = nose["roots"]
    assert isinstance(roots, list), "[tool.nose].roots must be a TOML array."

    for glob in excludes:
        assert isinstance(glob, str), "Every exclusion glob must be a string."
        literal = glob.removeprefix("**/").removesuffix("/**")
        assert literal, f"Exclude glob {glob!r} names no directory."
        matches = [
            path
            for root in roots
            if isinstance(root, str)
            for path in (REPO_ROOT / root).rglob(literal)
        ]
        assert matches, (
            f"Exclude glob {glob!r} names {literal!r}, which exists under no "
            "configured root; the gate would scan it rather than skip it."
        )
