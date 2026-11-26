"""Step definitions for the ``publish --expect-digests`` behavioural scenarios.

These scenarios drive :func:`lading.commands.publish.run` in process, through
the :class:`~tests.helpers.publish_digests.PublishRig` doubles, rather than
through the CLI subprocess the other publish scenarios use. The reason is the
second seam: crates.io downloads cannot be injected into a child process, and
no scenario may touch the network. The CLI wiring of the option itself is
covered in ``tests/unit/test_cli_expect_digests.py``.
"""

import dataclasses as dc
import logging
import typing as typ

import pytest
from pytest_bdd import given, parsers, then, when

from lading.commands.publish_errors import PublishPreflightError
from tests.helpers.publish_digests import (
    CRATE_NAMES,
    OTHER_ARCHIVE,
    PLAIN_ARCHIVE,
    PublishRig,
    digest,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

_INDEX_MISSING_ALPHA = (
    "error: failed to prepare local package for uploading\n\n"
    "Caused by:\n"
    '  failed to select a version for the requirement `alpha = "^0.1.0"`\n'
    "  candidate versions found which didn't match: 0.0.1\n"
    "  location searched: crates.io index\n"
    "  required by package `beta v0.1.0`\n"
)


@dc.dataclass(slots=True)
class DigestScenario:
    """Mutable state one scenario builds up before and after the run."""

    rig: PublishRig
    recorded: dict[str, bytes]
    default_registry: str | None = None
    summary: str | None = None
    error: PublishPreflightError | None = None


@pytest.fixture
def digest_scenario(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> DigestScenario:
    """Provide the scenario state around a fresh publish rig, capturing INFO logs."""
    caplog.set_level(logging.INFO)
    return DigestScenario(
        rig=PublishRig.create(tmp_path),
        recorded=dict.fromkeys(CRATE_NAMES, PLAIN_ARCHIVE),
    )


@given("a workspace whose crates package to their recorded digests")
def given_matching_workspace(digest_scenario: DigestScenario) -> None:
    """Record the digests the default archives have, and nothing else."""
    assert digest_scenario.rig.cargo.packaged, "the rig must package every crate"


@given(parsers.parse('cargo package builds different bytes for "{crate}"'))
def given_package_differs(digest_scenario: DigestScenario, crate: str) -> None:
    """Make ``cargo package`` build bytes other than the recorded ones."""
    digest_scenario.rig.cargo.packaged[crate] = OTHER_ARCHIVE


@given(parsers.parse('cargo publish uploads different bytes for "{crate}"'))
def given_upload_differs(digest_scenario: DigestScenario, crate: str) -> None:
    """Make ``cargo publish`` leave bytes other than the recorded ones."""
    digest_scenario.rig.cargo.uploaded[crate] = OTHER_ARCHIVE


@given(parsers.parse('crates.io already holds the recorded archive of "{crate}"'))
def given_published_identical(digest_scenario: DigestScenario, crate: str) -> None:
    """Publish the recorded bytes of ``crate`` on the fake registry."""
    digest_scenario.rig.registry.published[crate] = PLAIN_ARCHIVE


@given(parsers.parse('crates.io already holds a different archive of "{crate}"'))
def given_published_different(digest_scenario: DigestScenario, crate: str) -> None:
    """Publish bytes other than the recorded ones for ``crate``."""
    digest_scenario.rig.registry.published[crate] = OTHER_ARCHIVE


@given(parsers.parse('the digests file has no entry for "{crate}"'))
def given_missing_entry(digest_scenario: DigestScenario, crate: str) -> None:
    """Drop ``crate`` from the recorded digests."""
    del digest_scenario.recorded[crate]


@given(parsers.parse('the digests file has an extra entry for "{crate}"'))
def given_extra_entry(digest_scenario: DigestScenario, crate: str) -> None:
    """Record a digest for a crate the plan does not publish."""
    digest_scenario.recorded[crate] = PLAIN_ARCHIVE


@given(parsers.parse('the default registry is "{registry}"'))
def given_default_registry(digest_scenario: DigestScenario, registry: str) -> None:
    """Make Cargo's default registry something other than crates.io."""
    digest_scenario.default_registry = registry


@given(
    parsers.parse(
        'cargo package for "{crate}" fails because sibling "{sibling}" '
        "is not yet indexed"
    )
)
def given_package_index_miss(
    digest_scenario: DigestScenario, crate: str, sibling: str
) -> None:
    """Make ``cargo package`` fail the way an unpublished sibling does."""
    assert (crate, sibling) == ("beta", "alpha"), "the canned transcript is for beta"
    digest_scenario.rig.cargo.failing["package", crate] = (101, _INDEX_MISSING_ALPHA)


def _run(
    scenario: DigestScenario,
    monkeypatch: pytest.MonkeyPatch,
    *,
    live: bool,
    expecting: bool,
) -> None:
    """Run the publish and keep its summary or its publish error."""
    overrides: dict[str, object] = {"live": live}
    if expecting:
        overrides["expect_digests"] = scenario.rig.digests_file(scenario.recorded)
        overrides["default_registry"] = scenario.default_registry
    if not live:
        overrides["allow_unpublished_workspace_deps"] = True
    try:
        scenario.summary = scenario.rig.run(monkeypatch, **overrides)
    except PublishPreflightError as exc:
        scenario.error = exc


@when("I run a live publish expecting those digests")
def when_live_with_digests(
    digest_scenario: DigestScenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run a live publish with ``--expect-digests``."""
    _run(digest_scenario, monkeypatch, live=True, expecting=True)


@when("I run a dry-run publish expecting those digests")
def when_dry_run_with_digests(
    digest_scenario: DigestScenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run a dry-run publish with ``--expect-digests``."""
    _run(digest_scenario, monkeypatch, live=False, expecting=True)


@when("I run a live publish without expected digests")
def when_live_without_digests(
    digest_scenario: DigestScenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run a live publish without the option."""
    _run(digest_scenario, monkeypatch, live=True, expecting=False)


@then("the publish succeeds")
def then_succeeds(digest_scenario: DigestScenario) -> None:
    """Assert the run returned a summary."""
    assert digest_scenario.error is None, f"unexpected failure: {digest_scenario.error}"
    assert digest_scenario.summary is not None, "the run must return a summary"


@then(parsers.parse('the publish fails with "{text}"'))
def then_fails(digest_scenario: DigestScenario, text: str) -> None:
    """Assert the run raised a publish error containing ``text``."""
    assert digest_scenario.error is not None, "the run must have failed"
    assert text in str(digest_scenario.error), (
        f"the error must contain {text!r}, got: {digest_scenario.error}"
    )


@then(
    parsers.parse(
        'the error names crate "{crate}" with the expected and actual digests'
    )
)
def then_error_names_digests(digest_scenario: DigestScenario, crate: str) -> None:
    """Assert the error carries the crate and both digests."""
    message = str(digest_scenario.error)
    assert f"{crate} 0.1.0" in message, "the error must name the crate and version"
    assert digest(PLAIN_ARCHIVE) in message, "the expected digest must appear"
    assert digest(OTHER_ARCHIVE) in message, "the differing digest must appear"


def _names(text: str) -> list[str]:
    """Split a comma-separated crate list."""
    return [name.strip() for name in text.split(",")]


@then(parsers.parse('cargo publish ran for "{crates}"'))
def then_publish_ran(digest_scenario: DigestScenario, crates: str) -> None:
    """Assert which crates ``cargo publish`` ran for, in order."""
    assert digest_scenario.rig.cargo.crates_run("publish") == _names(crates), (
        "cargo publish must run for exactly the expected crates, in order"
    )


@then(parsers.parse('cargo package ran for "{crates}"'))
def then_package_ran(digest_scenario: DigestScenario, crates: str) -> None:
    """Assert which crates ``cargo package`` ran for, in order."""
    assert digest_scenario.rig.cargo.crates_run("package") == _names(crates), (
        "cargo package must run for exactly the expected crates, in order"
    )


@then(parsers.parse('the summary reports "{line}"'))
def then_summary_reports(digest_scenario: DigestScenario, line: str) -> None:
    """Assert a line of the archive-check summary, ignoring its detail suffix."""
    assert digest_scenario.summary is not None, "the run must have returned a summary"
    assert any(
        candidate.startswith(f"{line} (")
        for candidate in digest_scenario.summary.splitlines()
    ), f"no summary line reports {line!r}:\n{digest_scenario.summary}"


@then(parsers.parse('the summary does not report "{line}"'))
def then_summary_omits(digest_scenario: DigestScenario, line: str) -> None:
    """Assert a line is absent from the archive-check summary."""
    assert digest_scenario.summary is not None, "the run must have returned a summary"
    assert not any(
        candidate.startswith(f"{line} (")
        for candidate in digest_scenario.summary.splitlines()
    ), f"the summary must not report {line!r}:\n{digest_scenario.summary}"


@then("the summary has no archive checks")
def then_summary_has_no_checks(digest_scenario: DigestScenario) -> None:
    """Assert the summary is the one a run without the option prints."""
    assert digest_scenario.summary is not None, "the run must have returned a summary"
    assert "Archive checks" not in digest_scenario.summary, (
        "a run without the option must not report archive checks"
    )


@then(parsers.parse('an INFO log is emitted containing "{text}"'))
def then_info_logged(caplog: pytest.LogCaptureFixture, text: str) -> None:
    """Assert an INFO record contains ``text``."""
    assert any(
        text in record.getMessage()
        for record in caplog.records
        if record.levelno == logging.INFO
    ), f"no INFO record contains {text!r}"


@then("nothing was staged and no command ran")
def then_nothing_ran(digest_scenario: DigestScenario) -> None:
    """Assert the failure came before staging, packaging, or uploading."""
    assert digest_scenario.rig.cargo.calls == [], (
        "no cargo command may have run before the failure"
    )
    assert not digest_scenario.rig.build_directory.exists(), (
        "nothing may have been staged before the failure"
    )


@then("every cargo package and cargo publish command carried --locked")
def then_locked(digest_scenario: DigestScenario) -> None:
    """Assert ``--locked`` reached every package and publish command."""
    cargo = digest_scenario.rig.cargo
    commands = [*cargo.subcommands("package"), *cargo.subcommands("publish")]
    assert commands, "the scenario must have run cargo"
    assert all("--locked" in command for command in commands), commands


@then("no cargo command carried --locked")
def then_not_locked(digest_scenario: DigestScenario) -> None:
    """Assert ``--locked`` appears nowhere."""
    assert all(
        "--locked" not in command for command, _ in digest_scenario.rig.cargo.calls
    ), "no cargo command may carry --locked without the option"


@then("crates.io was not consulted at all")
def then_registry_untouched(digest_scenario: DigestScenario) -> None:
    """Assert no archive was looked up."""
    assert digest_scenario.rig.registry.requests == [], (
        "crates.io must not be consulted without the option"
    )


@then("crates.io was asked only for each crate's name and version")
def then_registry_asked_by_name_and_version(digest_scenario: DigestScenario) -> None:
    """Assert each lookup carried only a crate name and version."""
    assert digest_scenario.rig.registry.requests == [
        (name, "0.1.0") for name in CRATE_NAMES
    ], "each lookup must carry only a crate name and version"
