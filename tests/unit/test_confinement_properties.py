"""Property tests for the confinement policy's lexical and glob invariants.

The fixed regression cases elsewhere pin named spellings. These properties
cover the wider input spaces those cases sample: separators and prefixes for
the lexical validator, and small directory trees with regular files, symlinks
and hard links for confined glob expansion.
"""

import errno
import os
import shutil
import string
from pathlib import Path

import hypothesis.strategies as st
import pytest
from hypothesis import HealthCheck, given

from lading.utils.confinement import (
    GlobOptions,
    MatchExpectation,
    UnsafeBumpTargetError,
    expand_confined_glob,
    validate_relative_pattern,
)
from tests.helpers.hypothesis_settings import filesystem_property_settings

_separator = st.sampled_from(["/", "\\"])
_safe_component = st.text(
    alphabet=string.ascii_letters + string.digits + "._-",
    min_size=1,
    max_size=6,
).filter(lambda part: part not in {"..", "."})


@st.composite
def _unsafe_patterns(draw: st.DrawFn) -> tuple[str, str]:
    """Return a pattern that must be rejected and the reason it is unsafe."""
    prefix = draw(_safe_component)
    separator = draw(_separator)
    kind = draw(st.sampled_from(["parent", "absolute", "drive", "unc", "empty"]))
    match kind:
        case "parent":
            return separator.join((prefix, "..", draw(_safe_component))), "parent"
        case "absolute":
            return draw(st.sampled_from(["/", "\\"])) + prefix, "rooted"
        case "drive":
            return draw(st.sampled_from(["C:", "c:"])) + prefix, "drive"
        case "unc":
            return (
                draw(st.sampled_from(["//", "\\\\"])) + prefix + separator + "share",
                "unified naming convention",
            )
        case _:
            return draw(st.sampled_from(["", "   "])), "empty"


@given(case=_unsafe_patterns())
def test_validate_relative_pattern_rejects_unsafe_spellings(
    case: tuple[str, str],
) -> None:
    """Every unsafe spelling is refused with an unsafe-target error."""
    pattern, _reason = case
    with pytest.raises(UnsafeBumpTargetError):
        validate_relative_pattern(pattern, key="workspace.members[0]")


@given(
    parts=st.lists(_safe_component, min_size=1, max_size=5),
)
def test_validate_relative_pattern_returns_native_components(
    parts: list[str],
) -> None:
    """An accepted pattern yields non-empty native components without ``..``."""
    pattern = "/".join(parts)

    accepted = validate_relative_pattern(pattern, key="workspace.members[0]")

    assert accepted == tuple(parts), "accepted components must be preserved"
    assert ".." not in accepted, "an accepted pattern must not traverse upwards"
    assert all(os.sep not in part for part in accepted), (
        "an accepted component must not carry the host separator"
    )


@st.composite
def _glob_tree(draw: st.DrawFn) -> tuple[list[str], str]:
    """Return leaf names and the link shape to add to a small tree."""
    names = draw(
        st.lists(
            st.text(alphabet="abc", min_size=1, max_size=3),
            min_size=1,
            max_size=4,
            unique=True,
        )
    )
    shape = draw(st.sampled_from(["regular", "symlink", "hardlink"]))
    return [f"{name}.md" for name in names], shape


def _reset(root: Path) -> None:
    """Remove the previous example's tree."""
    if root.exists():
        shutil.rmtree(root)


def _add_link(root: Path, names: list[str], shape: str) -> None:
    """Add the requested link shape, skipping only an unsupported host."""
    match shape:
        case "symlink":
            try:
                (root / "link.md").symlink_to(root / names[0])
            except NotImplementedError:
                pytest.skip("symlinks are unavailable on this host")
            except OSError as exc:
                if exc.errno not in {errno.EPERM, errno.EACCES}:
                    raise
                pytest.skip(f"symlink creation is unavailable here: {exc}")
        case "hardlink":
            try:
                os.link(root / names[0], root / "hard.md")
            except OSError:
                pytest.skip("hard links are unavailable here")


@pytest.mark.parametrize("shape", ["regular", "symlink", "hardlink"])
@filesystem_property_settings(
    max_examples=20,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    names=st.lists(
        st.text(alphabet="abc", min_size=1, max_size=3),
        min_size=1,
        max_size=4,
        unique=True,
    )
)
def test_expand_confined_glob_returns_contained_sorted_unique_matches(
    tmp_path: Path,
    names: list[str],
    shape: str,
) -> None:
    """Matches stay contained and ordered, and every link shape is rejected."""
    names = [f"{name}.md" for name in names]
    root = tmp_path / "root"
    _reset(root)
    root.mkdir()
    for name in names:
        (root / name).write_text("x", encoding="utf-8")
    _add_link(root, names, shape)

    if shape != "regular":
        with pytest.raises(UnsafeBumpTargetError):
            expand_confined_glob(
                root,
                "*.md",
                key="docs",
                options=GlobOptions(expect=MatchExpectation.REGULAR_FILE),
            )
        return

    matches = expand_confined_glob(
        root,
        "*.md",
        key="docs",
        options=GlobOptions(expect=MatchExpectation.REGULAR_FILE),
    )

    displays = [match.display for match in matches]
    assert displays == sorted(set(displays)), "matches must be sorted and unique"
    expected = sorted(set(names))
    assert displays == expected, "every regular match must be returned once"
    for match in matches:
        assert match.path.resolve().is_relative_to(root.resolve()), (
            "every match must resolve beneath the confined root"
        )
