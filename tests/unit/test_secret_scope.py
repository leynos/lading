"""Tests for the explicit secret scope a digest-checked run hands its runners."""

import sys

from lading.runtime.redacting_runner import redacting_runner
from lading.utils.secret_scope import SecretScope, redact_active, use_secrets

_OUTER = "outer-secret-value-0123456789"
_LEARNED = "learned-secret-value-9876543210"


def _quiet_command() -> list[str]:
    """Return a command that prints nothing."""
    return [sys.executable, "-c", "pass"]


def test_a_runner_extends_only_the_scope_it_is_given() -> None:
    """A command's token reaches the given scope, never the open one."""
    given = SecretScope()

    with use_secrets((_OUTER,)):
        redacting_runner(given)(
            _quiet_command(), env={"CARGO_REGISTRY_TOKEN": _LEARNED}
        )
        shown = redact_active(_LEARNED)

    assert _LEARNED in given.secrets, "the given scope learns the token"
    assert shown == _LEARNED, "the enclosing open scope is left untouched"


def test_what_a_scope_learned_outlives_it_in_the_enclosing_scope() -> None:
    """A learned token stays hidden until the outermost scope ends."""
    with use_secrets((_OUTER,)):
        with use_secrets(()) as inner:
            inner.hide((_LEARNED,))
        after_inner = redact_active(_LEARNED)
    after_outer = redact_active(_LEARNED)

    assert _LEARNED not in after_inner, "the enclosing scope still hides it"
    assert after_outer == _LEARNED, "nothing stays hidden once every scope ends"


def test_an_inner_scope_sees_what_its_parent_learns_later() -> None:
    """A value hidden in the parent after the child opened is hidden there too."""
    with use_secrets(()) as outer, use_secrets(()) as inner:
        outer.hide((_LEARNED,))
        assert _LEARNED in inner.secrets, "the child reads its parent's values live"
        assert _LEARNED not in redact_active(_LEARNED), "and redacts with them"
