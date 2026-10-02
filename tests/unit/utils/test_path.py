"""Unit and property tests for :mod:`lading.utils.path`."""

import os
import string
from pathlib import Path

import hypothesis.strategies as st
from hypothesis import given

from lading.utils import normalize_workspace_root

_path_segment: st.SearchStrategy[str] = st.text(
    alphabet=string.ascii_lowercase + string.digits + "_-",
    min_size=1,
    max_size=12,
)

_relative_segments: st.SearchStrategy[list[str]] = st.lists(
    st.one_of(_path_segment, st.just("."), st.just("..")),
    min_size=1,
    max_size=5,
)


def test_none_defaults_to_cwd() -> None:
    """``None`` selects the resolved current working directory."""
    assert normalize_workspace_root(None) == Path.cwd().resolve(), (
        "a None workspace root must default to the resolved cwd"
    )


def test_tilde_is_expanded() -> None:
    """A leading ``~`` expands to the user home directory."""
    result = normalize_workspace_root(str(Path("~", "workspace")))

    assert result == Path.home().resolve() / "workspace", (
        "a leading tilde must expand to the resolved home directory"
    )


def test_accepts_path_instances() -> None:
    """`Path` inputs behave identically to string inputs."""
    candidate = Path("~", "ws")
    result = normalize_workspace_root(candidate)

    assert result == Path.home().resolve() / "ws", (
        "a Path instance must expand its tilde exactly as a string would"
    )
    assert result == normalize_workspace_root(str(candidate)), (
        "normalizing a Path must match normalizing its string form"
    )


@given(segments=_relative_segments)
def test_relative_inputs_resolve_to_absolute_paths(segments: list[str]) -> None:
    """Relative inputs resolve to a fully normalized, cwd-anchored path."""
    value = str(Path(*segments))
    result = normalize_workspace_root(value)

    # Independent invariants rather than a pathlib mirror of the implementation:
    # the output is absolute, retains no unresolved ``.``/``..`` segments,
    # anchors relative inputs at the cwd, and is a fixed point of further
    # normalization.
    assert result.is_absolute(), (
        "a relative workspace root must resolve to an absolute path"
    )
    assert ".." not in result.parts, (
        "the resolved path must retain no parent-directory segments"
    )
    assert "." not in result.parts, (
        "the resolved path must retain no current-directory segments"
    )
    assert result == normalize_workspace_root(Path.cwd() / value), (
        "a relative root must anchor at the cwd and resolve identically"
    )
    assert normalize_workspace_root(result) == result, (
        "normalizing an already-resolved path must be a fixed point"
    )


@given(segments=_relative_segments)
def test_redundant_separators_are_normalized(segments: list[str]) -> None:
    """Doubling separators does not change the resolved path."""
    value = str(Path(*segments))
    doubled = value.replace(os.sep, os.sep * 2)

    assert normalize_workspace_root(doubled) == normalize_workspace_root(value), (
        "doubling path separators must not change the resolved path"
    )


@given(segments=_relative_segments)
def test_tilde_prefix_expands_for_arbitrary_suffixes(segments: list[str]) -> None:
    """Expanding ``~`` is equivalent to substituting the literal home path."""
    tilde_value = str(Path("~", *segments))
    home_value = str(Path(Path.home(), *segments))
    result = normalize_workspace_root(tilde_value)

    # Independent invariants: the output is absolute, fully resolved, and the
    # ``~`` prefix expands to exactly the home directory.
    assert result.is_absolute(), (
        "a tilde-prefixed root must resolve to an absolute path"
    )
    assert ".." not in result.parts, (
        "the expanded home path must retain no parent-directory segments"
    )
    assert result == normalize_workspace_root(home_value), (
        "expanding a tilde must equal substituting the literal home path"
    )
