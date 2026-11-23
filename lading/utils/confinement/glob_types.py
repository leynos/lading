"""Policy types for non-following glob expansion.

These values select which final matches count and what an empty expansion
means. They live apart from the expansion engine so both the public entry point
and the engine can import them without a cycle.
"""

import dataclasses as dc
import enum

from lading.utils.confinement.walk import LinkPredicate, is_link_like

__all__ = ["GlobOptions", "MatchExpectation", "UnmatchedPolicy"]


class MatchExpectation(enum.Enum):
    """How a glob should treat final matches of each kind.

    ``REGULAR_FILE`` selects documentation files: regular files are returned
    and directory matches are ignored. ``DIRECTORY`` selects workspace-member
    directories: directories are returned and regular-file matches are ignored,
    matching Cargo's own member glob expansion. In both modes a matched special
    file is an error.
    """

    REGULAR_FILE = enum.auto()
    DIRECTORY = enum.auto()


class UnmatchedPolicy(enum.Enum):
    """What an expansion does when a pattern matches nothing."""

    IGNORE = enum.auto()
    ERROR = enum.auto()


@dc.dataclass(frozen=True, slots=True)
class GlobOptions:
    """Matching policy for :func:`expand_confined_glob`.

    Attributes
    ----------
    expect : MatchExpectation
        Kind of final match to select.
    unmatched : UnmatchedPolicy
        Whether a pattern that matches nothing is an error.
    link_predicate : LinkPredicate
        Predicate identifying symlinks and junctions.
    """

    expect: MatchExpectation = MatchExpectation.REGULAR_FILE
    unmatched: UnmatchedPolicy = UnmatchedPolicy.IGNORE
    link_predicate: LinkPredicate = is_link_like
