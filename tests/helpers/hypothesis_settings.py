"""Shared Hypothesis settings for filesystem-backed property tests.

Hypothesis enforces a per-example deadline (200 ms by default) so that a
property which suddenly becomes slow is reported as a failure rather than
merely running slowly. That default suits a property whose elapsed time is
itself part of the contract; no property in this suite asserts duration, so
the suite profile disables the deadline for the run (issue #270). It is the
wrong check for a property that creates, rewrites, scans, resolves or removes
real filesystem state in every example: the time then measures disk latency
and cold import cost, not the invariant.

:func:`filesystem_property_settings` remains the explicit per-property opt-out
for that boundary. The suite's own ``lading`` profile, registered in
``tests/conftest.py``, disables the deadline for every property because no
property here asserts duration (issue #270); the factory therefore keeps the
filesystem exemption working when an explicit ``--hypothesis-profile``
replaces that profile. It changes no global state, so whether an in-memory
property is timed remains the active profile's decision.

The measurement that motivated the seam: the first example of the
manifest-selection property took 281 ms against the 200 ms default on a cold
start, and 51 ms when the run was repeated on a warm host. Raising the number
would only move the flake; the deadline itself is the wrong instrument here.

Health-check suppression is a separate decision. Filesystem access alone does
not require ``HealthCheck.function_scoped_fixture``; a property that uses no
pytest fixture needs no suppression, while one that reuses a function-scoped
``tmp_path`` across examples does. Omitting the argument inherits the active
profile's suppressions, which matters because the ``ci`` profile suppresses
``HealthCheck.too_slow``. Pass an explicit sequence, including an empty one,
to choose suppressions yourself.

Example isolation also remains the property's responsibility: this helper
does not create, reset or clean up any state, so a shared ``tmp_path`` still
needs an explicit reset between examples.
"""

import collections.abc as cabc
import typing as typ

from hypothesis import settings

if typ.TYPE_CHECKING:
    from hypothesis import HealthCheck


def filesystem_property_settings(
    *,
    max_examples: int | None = None,
    suppress_health_check: cabc.Sequence[HealthCheck] | None = None,
) -> settings:
    """Return settings for a property that performs real filesystem I/O.

    Use this only when each example creates, rewrites, scans, resolves or
    removes real filesystem state. Building ``Path`` objects under
    ``tmp_path`` without touching the disk does not qualify; whether those
    properties are timed remains the active profile's decision.

    Parameters
    ----------
    max_examples : int, optional
        Number of examples to generate, pinning the property's existing
        budget. Omit it to inherit the active profile's budget.
    suppress_health_check : collections.abc.Sequence[HealthCheck], optional
        Health checks to suppress. Omit it to inherit the active profile's
        suppressions. Pass an explicit sequence, including an empty one, to
        choose exactly; use ``[HealthCheck.function_scoped_fixture]`` only
        when the property reuses a function-scoped fixture such as
        ``tmp_path`` across examples.

    Returns
    -------
    hypothesis.settings
        Settings with ``deadline=None`` and whichever of the other two
        arguments the caller supplied. Unsupplied settings are inherited from
        the active profile.

    Notes
    -----
    The suite profile disables the deadline for every property, so this
    factory mainly keeps the filesystem exemption in place when an explicit
    ``--hypothesis-profile`` replaces that profile. Hypothesis also selects its
    built-in ``ci`` profile in CI environments, which disables deadlines and
    suppresses ``HealthCheck.too_slow``. The factory changes no global state.

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

    Leave the decorator off for a property that only computes in memory; the
    active profile decides its deadline. The illustration is a code block
    rather than a doctest because ``make test``
    runs ``--doctest-modules``, and an executable example here would need a
    real property to decorate.
    """
    options: dict[str, typ.Any] = {"deadline": None}
    if max_examples is not None:
        options["max_examples"] = max_examples
    if suppress_health_check is not None:
        options["suppress_health_check"] = suppress_health_check
    return settings(**options)
