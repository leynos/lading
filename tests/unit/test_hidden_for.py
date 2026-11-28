"""Tests for the entry point's secret scope taking its environment explicitly."""

import typing as typ

from lading.cli_environment import hidden_for
from lading.utils.secret_scope import redact_active

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest

_GIVEN = "given-registry-token-0123456789"
_AMBIENT = "ambient-registry-token-9876543210"
_CHECKED = ("publish", "--expect-digests", "digests.txt")


def test_hidden_for_reads_only_the_environment_it_is_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The mapping passed in decides what is hidden, not the process's own."""
    monkeypatch.setenv("CARGO_REGISTRY_TOKEN", _AMBIENT)
    environ = {"CARGO_REGISTRY_TOKEN": _GIVEN, "CARGO_HOME": str(tmp_path)}

    with hidden_for(_CHECKED, environ):
        given, ambient = redact_active(_GIVEN), redact_active(_AMBIENT)

    assert _GIVEN not in given, "the given environment's token is hidden"
    assert ambient == _AMBIENT, "the ambient environment is never consulted"


def test_hidden_for_hides_nothing_without_the_option(tmp_path: Path) -> None:
    """Without ``--expect-digests`` the scope is a no-op whatever it is given."""
    environ = {"CARGO_REGISTRY_TOKEN": _GIVEN, "CARGO_HOME": str(tmp_path)}

    with hidden_for(("publish",), environ):
        shown = redact_active(_GIVEN)

    assert shown == _GIVEN, "an ordinary run hides nothing"
