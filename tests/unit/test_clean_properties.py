"""Property cover for what `lading clean` takes into scope.

The example cases in `test_clean.py` each name one thing a careless glob
would have taken: a directory without the prefix, a symbolic link, a regular
file, a nested match. Each is a single hand-picked arrangement, so together
they say the rule holds for four directories laid out four particular ways.

This says it holds for arbitrary ones. The command deletes, so the property
worth stating is an equality rather than an inclusion: discovery returns
exactly the immediate children that carry the prefix and are real
directories, no more and no fewer, whatever else is sitting beside them.
"""

from __future__ import annotations

import string
import tempfile
from pathlib import Path

import hypothesis.strategies as st
from hypothesis import HealthCheck, given, settings

from lading.commands import clean
from lading.commands.publish_staging import STAGING_PREFIX

#: Entry names are short and drawn from a small alphabet so that collisions
#: between the prefixed and unprefixed variants actually occur: the property
#: is about which entries are chosen, and near-misses are where a rule that
#: matched on the wrong thing would show.
_ENTRY_STEM = st.text(
    alphabet=string.ascii_lowercase + string.digits + "-",
    min_size=1,
    max_size=6,
)

#: The three shapes an immediate child can take. Only one of them is in
#: scope, and the other two are the ones this command must refuse.
_ENTRY_KIND = st.sampled_from(["directory", "file", "symlink"])

_ENTRIES = st.lists(
    st.tuples(st.booleans(), _ENTRY_STEM, _ENTRY_KIND),
    min_size=0,
    max_size=12,
)


def _create(location: Path, name: str, kind: str, link_target: Path) -> None:
    """Create one immediate child of ``location`` of the requested shape."""
    entry = location / name
    if kind == "directory":
        entry.mkdir()
        # A nested match, so the property also pins the one-level rule: a
        # prefixed directory inside a prefixed directory is not its own
        # leftover, and recursing would report the same bytes twice.
        (entry / f"{STAGING_PREFIX}nested").mkdir()
    elif kind == "file":
        entry.write_text("x", encoding="utf-8")
    else:
        entry.symlink_to(link_target, target_is_directory=True)


@given(entries=_ENTRIES)
@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_discovery_returns_exactly_the_prefixed_real_directories(
    entries: list[tuple[bool, str, str]],
    tmp_path: Path,
) -> None:
    """Discovery must return the prefixed real directories and nothing else.

    Stated as an equality rather than a containment, because both directions
    cost something real. Returning too much means deleting a directory that
    was never a staged workspace; returning too little means the leftovers
    this command exists to sweep stay on the disk and the report says the
    space has been reclaimed.
    """
    location = Path(tempfile.mkdtemp(dir=tmp_path))
    link_target = Path(tempfile.mkdtemp(dir=tmp_path))
    expected: set[str] = set()
    made: set[str] = set()
    for prefixed, stem, kind in entries:
        name = f"{STAGING_PREFIX}{stem}" if prefixed else stem
        if name in made:
            # Hypothesis draws names independently, so the same one recurs.
            # The first shape created is the one on disk.
            continue
        made.add(name)
        _create(location, name, kind, link_target)
        if prefixed and kind == "directory":
            expected.add(name)

    found = clean.find_leftovers(location)

    assert {leftover.path.name for leftover in found} == expected, (
        f"discovery disagreed with the scope rule for {sorted(made)}"
    )
    assert [leftover.path.name for leftover in found] == sorted(expected), (
        "the report was not in name order"
    )
    assert all(leftover.path.parent == location for leftover in found), (
        "discovery reached outside the directory it was given"
    )
