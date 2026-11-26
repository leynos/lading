"""Unit tests for parsing and validating ``--expect-digests`` files."""

import hashlib
import typing as typ
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from lading.commands import publish_digests
from lading.commands.publish_digests import ExpectedDigests
from lading.commands.publish_errors import ExpectedDigestsError

from .conftest import make_crate, plan_with_crates

if typ.TYPE_CHECKING:
    from lading.commands.publish_plan import PublishPlan

_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64


def _plan(tmp_path: Path, *names: str, exclude: tuple[str, ...] = ()) -> PublishPlan:
    """Return a plan publishing ``names`` (all at version 0.1.0)."""
    crates = tuple(make_crate(tmp_path / "workspace", name) for name in names)
    return plan_with_crates(tmp_path / "workspace", crates, exclude=exclude)


def test_parse_reads_sha256sum_entries_and_ignores_blank_lines() -> None:
    """Entries parse into lowercase digests keyed by file name."""
    text = (
        f"\n{_DIGEST_A}  alpha-0.1.0.crate\n   \n"
        f"{_DIGEST_B.upper()}  beta-0.1.0.crate\n"
    )

    parsed = publish_digests.parse_digest_text(text, source="digests.txt")

    assert parsed == {
        "alpha-0.1.0.crate": _DIGEST_A,
        "beta-0.1.0.crate": _DIGEST_B,
    }, "blank lines are ignored and digests are normalised to lowercase"


def test_parse_accepts_crlf_line_endings() -> None:
    """A file written on Windows parses the same as one written elsewhere."""
    parsed = publish_digests.parse_digest_text(
        f"{_DIGEST_A}  alpha-0.1.0.crate\r\n", source="digests.txt"
    )

    assert parsed == {"alpha-0.1.0.crate": _DIGEST_A}, "CRLF must not reach the name"


@pytest.mark.parametrize(
    "line",
    [
        "not a digest",
        f"{'a' * 63}  alpha-0.1.0.crate",
        f"{'a' * 65}  alpha-0.1.0.crate",
        f"{'g' * 64}  alpha-0.1.0.crate",
        f"{_DIGEST_A} alpha-0.1.0.crate",
        f"{_DIGEST_A}  ",
        f"{_DIGEST_A}  alpha 0.1.0.crate",
        f" {_DIGEST_A}  alpha-0.1.0.crate",
    ],
    ids=[
        "prose",
        "short_digest",
        "long_digest",
        "non_hex",
        "single_space",
        "no_name",
        "name_with_space",
        "leading_space",
    ],
)
def test_parse_rejects_malformed_lines(line: str) -> None:
    """Each deviation from the ``sha256sum`` shape is reported with its line."""
    with pytest.raises(ExpectedDigestsError) as excinfo:
        publish_digests.parse_digest_text(
            f"{_DIGEST_A}  ok-0.1.0.crate\n{line}\n", source="digests.txt"
        )

    message = str(excinfo.value)
    assert "Malformed digest entries in digests.txt" in message, (
        "the message must name the file"
    )
    assert "line 2:" in message, "the message must name the offending line"
    assert "line 1:" not in message, "the well-formed line must not be reported"


def test_parse_reports_every_malformed_line_not_only_the_first() -> None:
    """One run lists all the faults so the file is fixed in one pass."""
    with pytest.raises(ExpectedDigestsError) as excinfo:
        publish_digests.parse_digest_text("one\ntwo\n", source="digests.txt")

    assert "line 1: 'one'" in str(excinfo.value), (
        "the first malformed line must be listed"
    )
    assert "line 2: 'two'" in str(excinfo.value), (
        "the second malformed line must be listed"
    )


def test_parse_rejects_a_name_listed_twice() -> None:
    """Two entries for one archive are ambiguous, even when they agree."""
    text = f"{_DIGEST_A}  alpha-0.1.0.crate\n{_DIGEST_A}  alpha-0.1.0.crate\n"

    with pytest.raises(ExpectedDigestsError, match="Duplicate digest entries"):
        publish_digests.parse_digest_text(text, source="digests.txt")


@given(
    entries=st.dictionaries(
        keys=st.from_regex(
            r"[a-z][a-z0-9_]{0,12}-[0-9]\.[0-9]\.[0-9]\.crate", fullmatch=True
        ),
        values=st.binary(max_size=32).map(
            lambda data: hashlib.sha256(data).hexdigest()
        ),
        max_size=6,
    )
)
def test_parse_round_trips_sha256sum_output(entries: dict[str, str]) -> None:
    """Whatever ``sha256sum`` would print for these files parses back to them."""
    text = "".join(f"{digest}  {name}\n" for name, digest in entries.items())

    assert publish_digests.parse_digest_text(text, source="d") == entries, (
        "parsing must invert sha256sum output"
    )


def test_load_reads_the_file(tmp_path: Path) -> None:
    """``load_expected_digests`` returns the parsed digests and their source."""
    path = tmp_path / "digests.txt"
    path.write_text(f"{_DIGEST_A}  alpha-0.1.0.crate\n", encoding="utf-8")

    expected = publish_digests.load_expected_digests(path)

    assert expected.source == str(path), "messages must be able to name the file"
    assert expected.digests == {"alpha-0.1.0.crate": _DIGEST_A}, (
        "the file's entries must be parsed"
    )


def test_load_reports_an_unreadable_file(tmp_path: Path) -> None:
    """A missing file is an expected-digests error naming the path."""
    path = tmp_path / "absent.txt"

    with pytest.raises(ExpectedDigestsError) as excinfo:
        publish_digests.load_expected_digests(path)

    assert f"Cannot read expected digests from {path}" in str(excinfo.value), (
        "the message must name the unreadable path"
    )
    assert isinstance(excinfo.value.__cause__, OSError), "the cause must be kept"


def test_load_reports_a_file_that_is_not_text(tmp_path: Path) -> None:
    """Undecodable bytes are reported rather than escaping as a decode error."""
    path = tmp_path / "digests.bin"
    path.write_bytes(b"\xff\xfe\x00")

    with pytest.raises(ExpectedDigestsError, match="Cannot read expected digests"):
        publish_digests.load_expected_digests(path)


def test_validate_accepts_exactly_the_planned_archives(tmp_path: Path) -> None:
    """One entry per publishable crate is the only accepted shape."""
    plan = _plan(tmp_path, "alpha", "beta")
    expected = ExpectedDigests(
        "d", {"alpha-0.1.0.crate": _DIGEST_A, "beta-0.1.0.crate": _DIGEST_B}
    )

    publish_digests.validate_against_plan(expected, plan)


def test_validate_reports_missing_and_extra_entries_together(tmp_path: Path) -> None:
    """Every discrepancy is listed, missing entries first."""
    plan = _plan(tmp_path, "alpha", "beta")
    expected = ExpectedDigests(
        "d", {"alpha-0.1.0.crate": _DIGEST_A, "stray-9.9.9.crate": _DIGEST_B}
    )

    with pytest.raises(ExpectedDigestsError) as excinfo:
        publish_digests.validate_against_plan(expected, plan)

    message = str(excinfo.value)
    assert "no entry for beta-0.1.0.crate" in message, (
        "the missing entry must be listed"
    )
    assert "entry for stray-9.9.9.crate, which is not in the plan" in message, (
        "the extra entry must be listed"
    )
    assert "alpha-0.1.0.crate" not in message, "a matched entry is not a problem"


def test_validate_treats_an_excluded_crate_as_not_in_the_plan(tmp_path: Path) -> None:
    """An entry for a crate the configuration excludes is an extra entry."""
    plan = _plan(tmp_path, "alpha", "beta", exclude=("beta",))
    expected = ExpectedDigests(
        "d", {"alpha-0.1.0.crate": _DIGEST_A, "beta-0.1.0.crate": _DIGEST_B}
    )

    with pytest.raises(
        ExpectedDigestsError, match="beta-0\\.1\\.0\\.crate, which is not"
    ):
        publish_digests.validate_against_plan(expected, plan)


def test_validate_matches_on_version_as_well_as_name(tmp_path: Path) -> None:
    """An entry for another version of a planned crate does not satisfy it."""
    plan = _plan(tmp_path, "alpha")
    expected = ExpectedDigests("d", {"alpha-0.2.0.crate": _DIGEST_A})

    with pytest.raises(ExpectedDigestsError) as excinfo:
        publish_digests.validate_against_plan(expected, plan)

    assert "no entry for alpha-0.1.0.crate" in str(excinfo.value), (
        "the planned version must be reported missing"
    )
    assert "alpha-0.2.0.crate, which is not in the plan" in str(excinfo.value), (
        "the other version must be reported extra"
    )


def test_sha256_file_matches_hashlib_across_chunk_boundaries(tmp_path: Path) -> None:
    """Hashing in chunks gives the same digest as hashing in one piece."""
    payload = b"crate" * (publish_digests._READ_CHUNK_BYTES // 5 + 17)
    path = tmp_path / "big.crate"
    path.write_bytes(payload)

    assert publish_digests.sha256_file(path) == hashlib.sha256(payload).hexdigest(), (
        "chunked hashing must equal one-shot hashing"
    )


def test_digest_for_looks_up_by_name_and_version(tmp_path: Path) -> None:
    """``digest_for`` keys on ``<name>-<version>.crate``."""
    crate = make_crate(tmp_path, "alpha")
    expected = ExpectedDigests("d", {"alpha-0.1.0.crate": _DIGEST_A})

    assert expected.digest_for(crate) == _DIGEST_A, (
        "the lookup must key on name and version"
    )
