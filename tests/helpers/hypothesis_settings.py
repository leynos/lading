"""Shared Hypothesis settings for filesystem-backed property tests.

Hypothesis enforces a per-example deadline (200 ms by default) so that a
property which suddenly becomes slow is reported as a failure rather than
merely running slowly. That default is the right check for a property whose
work is pure computation. It is the wrong check for a property that creates,
rewrites, scans, resolves or removes real filesystem state in every example:
the time then measures disk latency and cold import cost, not the invariant.

:func:`filesystem_property_settings` is the one place that decision is made.
Opting in is explicit and per property; there is no profile or global
override, so an in-memory property keeps the default deadline and still
detects a real regression.

The measurement that motivated the seam: the first example of the
manifest-selection property took 281 ms against the 200 ms default on a cold
start, and 51 ms when the run was repeated on a warm host. Raising the number
would only move the flake; the deadline itself is the wrong instrument here.

Health-check suppression is a separate decision. Filesystem access alone does
not require ``HealthCheck.function_scoped_fixture``; a property that uses no
pytest fixture needs no suppression, while one that reuses a function-scoped
``tmp_path`` across examples does. Callers pass exactly the list they need.

Example isolation also remains the property's responsibility: this helper
changes only timing. It does not create, reset or clean up any state, so a
shared ``tmp_path`` still needs an explicit reset between examples.
"""

import collections.abc as cabc
import typing as typ

from hypothesis import settings

if typ.TYPE_CHECKING:
    from hypothesis import HealthCheck


def filesystem_property_settings(
    *,
    max_examples: int,
    suppress_health_check: cabc.Sequence[HealthCheck] = (),
) -> settings:
    """Return settings for a property that performs real filesystem I/O.

    Use this only when each example creates, rewrites, scans, resolves or
    removes real filesystem state. Building ``Path`` objects under
    ``tmp_path`` without touching the disk does not qualify; those properties
    keep the default deadline so a genuine slowdown is still reported.

    Parameters
    ----------
    max_examples : int
        Number of examples to generate, preserving the property's existing
        budget.
    suppress_health_check : collections.abc.Sequence[HealthCheck], optional
        Health checks to suppress, by default no suppression. Pass
        ``[HealthCheck.function_scoped_fixture]`` only when the property
        reuses a function-scoped fixture such as ``tmp_path`` across
        examples.

    Returns
    -------
    hypothesis.settings
        Settings with the supplied example budget, the given suppressions and
        ``deadline=None``. Every other setting keeps its Hypothesis default.

    Examples
    --------
    Opt a property in when every example writes to the disk:

    .. code-block:: python

        @given(names=st.lists(_crate_name))
        @filesystem_property_settings(
            max_examples=20,
            suppress_health_check=[HealthCheck.function_scoped_fixture],
        )
        def test_manifests_round_trip(tmp_path, names):
            ...

    Leave the decorator off for a property that only computes in memory. The
    illustration is a code block rather than a doctest because ``make test``
    runs ``--doctest-modules``, and an executable example here would need a
    real property to decorate.
    """
    return settings(
        max_examples=max_examples,
        deadline=None,
        suppress_health_check=suppress_health_check,
    )
