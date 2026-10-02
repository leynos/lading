"""Hypothesis property tests for the in-process metrics accumulator.

The accumulator carries two non-obvious invariants that example-based tests
only spot-check:

- **Label-order invariance** — counters are keyed by metric name plus a
  *sorted* tuple of label pairs, so the order labels are supplied in must never
  change which counter is addressed.
- **Accumulation** — repeated increments for the same label set must sum,
  independent of interleaving with other label sets.

A third property guards the test seam itself: :func:`snapshot` must return an
isolated copy that later mutation and :func:`reset` cannot disturb.

Each property resets the process-global registry at the start of the body
rather than via a function-scoped fixture, which Hypothesis discourages when
combined with ``@given``.
"""

import collections
import operator

from hypothesis import given, settings
from hypothesis import strategies as st

from lading.utils import metrics

# ``increment_counter(name, *, amount=1, **labels)`` reserves these keyword
# names. ``amount`` is supplied explicitly at every ``**`` call site below,
# which both states the default and makes a colliding label key an immediate
# ``TypeError`` rather than a silently consumed counter increment.
_RESERVED_LABEL_KEYS = frozenset({"name", "amount"})

# Label keys and values span the printable ASCII range; keys within a single
# label set are kept unique so a dict round-trip cannot collapse them.
_label_text = st.text(
    alphabet=st.characters(min_codepoint=33, max_codepoint=126),
    min_size=1,
    max_size=8,
)
_label_key = _label_text.filter(lambda key: key not in _RESERVED_LABEL_KEYS)
_label_pairs = st.lists(
    st.tuples(_label_key, _label_text),
    min_size=2,
    max_size=5,
    unique_by=operator.itemgetter(0),
)


@given(pairs=_label_pairs, data=st.data())
@settings(max_examples=100, deadline=None)
def test_label_order_does_not_affect_counter_identity(
    pairs: list[tuple[str, str]], data: st.DataObject
) -> None:
    """Any permutation of label kwargs addresses the same counter."""
    metrics.reset()
    permuted = data.draw(st.permutations(pairs))

    metrics.increment_counter("prop.identity", amount=1, **dict(pairs))

    assert metrics.counter_value("prop.identity", **dict(permuted)) == 1, (
        "permuting label order must address the same counter"
    )
    assert metrics.snapshot() == {("prop.identity", tuple(sorted(pairs))): 1}, (
        "the registry key must hold labels sorted by key"
    )


@given(
    increments=st.lists(
        st.tuples(_label_text, st.integers(min_value=1, max_value=1000)),
        min_size=1,
        max_size=30,
    )
)
@settings(max_examples=100, deadline=None)
def test_increments_accumulate_per_label(
    increments: list[tuple[str, int]],
) -> None:
    """The final counter for each label equals the sum of its increments."""
    metrics.reset()
    expected: collections.Counter[str] = collections.Counter()

    for subcommand, amount in increments:
        metrics.increment_counter("prop.count", amount=amount, subcommand=subcommand)
        expected[subcommand] += amount

    for subcommand, total in expected.items():
        assert metrics.counter_value("prop.count", subcommand=subcommand) == total, (
            "each label set's counter must equal the sum of its increments"
        )


@given(pairs=_label_pairs)
@settings(max_examples=100, deadline=None)
def test_snapshot_is_an_isolated_copy(pairs: list[tuple[str, str]]) -> None:
    """A snapshot is unaffected by later increments and registry resets."""
    metrics.reset()
    metrics.increment_counter("prop.snapshot", amount=1, **dict(pairs))

    captured = metrics.snapshot()
    expected = dict(captured)

    metrics.increment_counter("prop.snapshot", amount=1, **dict(pairs))
    metrics.reset()

    assert captured == expected, (
        "a snapshot must be unaffected by later increments and resets"
    )
