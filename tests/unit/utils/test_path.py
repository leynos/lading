"""Unit and property tests for :mod:`lading.utils.path`."""

from __future__ import annotations

import os
import string
from pathlib import Path

import hypothesis.strategies as st
from hypothesis import given

from lading.utils import normalise_workspace_root

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
    assert normalise_workspace_root(None) == Path.cwd().resolve()


def test_tilde_is_expanded() -> None:
    """A leading ``~`` expands to the user home directory."""
    result = normalise_workspace_root(str(Path("~", "workspace")))

    assert result == Path.home().resolve() / "workspace"


def test_accepts_path_instances() -> None:
    """`Path` inputs behave identically to string inputs."""
    candidate = Path("~", "ws")
    result = normalise_workspace_root(candidate)

    assert result == Path.home().resolve() / "ws"
    assert result == normalise_workspace_root(str(candidate))


@given(segments=_relative_segments)
def test_relative_inputs_resolve_to_absolute_paths(segments: list[str]) -> None:
    """Relative inputs resolve to a fully normalised, cwd-anchored path."""
    value = str(Path(*segments))
    result = normalise_workspace_root(value)

    # Independent invariants rather than a pathlib mirror of the implementation:
    # the output is absolute, retains no unresolved ``.``/``..`` segments,
    # anchors relative inputs at the cwd, and is a fixed point of further
    # normalisation.
    assert result.is_absolute()
    assert ".." not in result.parts
    assert "." not in result.parts
    assert result == normalise_workspace_root(Path.cwd() / value)
    assert normalise_workspace_root(result) == result


@given(segments=_relative_segments)
def test_redundant_separators_are_normalised(segments: list[str]) -> None:
    """Doubling separators does not change the resolved path."""
    value = str(Path(*segments))
    doubled = value.replace(os.sep, os.sep * 2)

    assert normalise_workspace_root(doubled) == normalise_workspace_root(value)


@given(segments=_relative_segments)
def test_tilde_prefix_expands_for_arbitrary_suffixes(segments: list[str]) -> None:
    """Expanding ``~`` is equivalent to substituting the literal home path."""
    tilde_value = str(Path("~", *segments))
    home_value = str(Path(Path.home(), *segments))
    result = normalise_workspace_root(tilde_value)

    # Independent invariants: the output is absolute, fully resolved, and the
    # ``~`` prefix expands to exactly the home directory.
    assert result.is_absolute()
    assert ".." not in result.parts
    assert result == normalise_workspace_root(home_value)
