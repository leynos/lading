"""Tests for choosing a redaction marker that no known secret can show through.

A named-registry Cargo token can be any string, including ``redacted``, so the
marker must adapt rather than drop such a value and let it reach the output.
"""

import pytest

from lading.runtime.env_redaction import redact_environment
from lading.utils.redaction import REDACTED, SecretRedactor, marker_for, redact_text

_REAL = "real-token-value"


@pytest.mark.parametrize(
    "secret",
    [
        REDACTED,
        "redacted",
        "act",
        "<red",
        "ted>",
        "ed>x",
        "y<re",
        f"a{REDACTED}b",
        "<",
        ">",
    ],
    ids=[
        "whole_marker",
        "the_word_redacted",
        "inside_marker",
        "marker_prefix",
        "marker_suffix",
        "starts_with_marker_suffix",
        "ends_with_marker_prefix",
        "contains_marker",
        "open_bracket",
        "close_bracket",
    ],
)
def test_a_secret_overlapping_the_marker_is_still_hidden(secret: str) -> None:
    """One-shot and character-by-character redaction both hide the secret."""
    text = f"before {secret} and {_REAL} after {secret}{secret}"

    shown = redact_text(text, (secret, _REAL))
    redactor = SecretRedactor((secret, _REAL))
    streamed = "".join(redactor.feed(char) for char in text) + redactor.flush()

    assert secret not in shown, "the overlapping secret must not appear in the output"
    assert _REAL not in shown, "the real token must not appear either"
    assert marker_for((secret, _REAL)) in shown, "a marker must stand in their place"
    assert marker_for((secret, _REAL)) != REDACTED, "the default marker is replaced"
    assert streamed == shown, "character-by-character streaming must agree"


def test_the_default_marker_is_kept_when_no_secret_can_overlap_it() -> None:
    """Ordinary tokens keep the familiar ``<redacted>`` placeholder."""
    assert marker_for((_REAL, "")) == REDACTED, "the default is kept"
    assert redact_text(f"a {_REAL} b", (_REAL,)) == f"a {REDACTED} b", (
        "ordinary tokens are replaced by the default marker"
    )


@pytest.mark.parametrize(
    ("secrets", "expected"),
    [
        (("redacted",), "<********>"),
        (("redacted", "*"), "<########>"),
        (("<redacted>",), "**********"),
    ],
    ids=["framed", "next_filler", "unframed"],
)
def test_the_fallback_marker_is_deterministic(
    secrets: tuple[str, ...], expected: str
) -> None:
    """The fallback depends only on the secrets, never on their order."""
    assert marker_for(secrets) == expected, "the fallback must be predictable"
    assert marker_for(reversed(secrets)) == expected, "order must not matter"


def test_a_fallback_marker_uses_only_a_character_no_secret_contains() -> None:
    """The fallback cannot overlap any secret, because it shares no character."""
    secrets = ("redacted", "<>", "*#~=-_.+!$%&@^|:;?/,'\"`()[]{}")

    marker = marker_for(secrets)

    assert not set(marker) & set("".join(secrets)), (
        "the marker must share no character with any secret"
    )


def test_secrets_using_every_character_fail_closed() -> None:
    """With no character left to build a marker from, redaction refuses."""
    every = "".join(map(chr, [*range(0x21, 0xD800), *range(0xE000, 0x110000)]))

    with pytest.raises(ValueError, match="every character"):
        marker_for(("redacted", every))


def test_masked_environment_values_use_the_adaptive_marker() -> None:
    """A masked variable cannot show a known secret through its placeholder."""
    env = {"CARGO_REGISTRIES_MINE_TOKEN": "redacted", "MIRROR": "x redacted y"}

    shown = redact_environment(env, ("redacted",))

    assert "redacted" not in "".join(shown.values()), "the secret must not show"
    assert shown["CARGO_REGISTRIES_MINE_TOKEN"] == marker_for(("redacted",)), (
        "the masked value must use the same marker as value redaction"
    )
