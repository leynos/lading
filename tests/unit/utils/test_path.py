"""Unit and property tests for :mod:`lading.utils.path`."""

from __future__ import annotations

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

_relative_path: st.SearchStrategy[str] = st.lists(
    st.one_of(_path_segment, st.just("."), st.just("..")),
    min_size=1,
    max_size=5,
).map("/".join)


def _pathlib_reference(value: Path | str) -> Path:
    """Compute the expected normalisation using pathlib primitives only."""
    return Path(value).expanduser().resolve(strict=False)


def test_none_defaults_to_cwd() -> None:
    """``None`` selects the resolved current working directory."""
    assert normalise_workspace_root(None) == Path.cwd().resolve()


def test_tilde_is_expanded() -> None:
    """A leading ``~`` expands to the user home directory."""
    result = normalise_workspace_root("~/workspace")

    assert result == Path.home().resolve() / "workspace"


def test_accepts_path_instances() -> None:
    """`Path` inputs behave identically to string inputs."""
    assert normalise_workspace_root(Path("~/ws")) == normalise_workspace_root("~/ws")


@given(value=_relative_path)
def test_relative_inputs_resolve_to_absolute_paths(value: str) -> None:
    """Any relative input yields an absolute path matching the reference."""
    result = normalise_workspace_root(value)

    assert result.is_absolute()
    assert result == _pathlib_reference(value)


@given(value=_relative_path)
def test_redundant_separators_are_normalised(value: str) -> None:
    """Doubling separators does not change the resolved path."""
    doubled = value.replace("/", "//")

    assert normalise_workspace_root(doubled) == normalise_workspace_root(value)


@given(value=_relative_path)
def test_tilde_prefix_expands_for_arbitrary_suffixes(value: str) -> None:
    """``~/suffix`` inputs are anchored beneath the home directory."""
    result = normalise_workspace_root(f"~/{value}")

    assert result.is_absolute()
    assert result == _pathlib_reference(f"~/{value}")
