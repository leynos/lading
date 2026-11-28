"""Ordering, registry, and reporting tests for ``publish --expect-digests``.

These cover where in a run each check happens: per-crate target directories,
the registry pin on both cargo commands, the published-version check before
packaging, validation before the pre-flight, and what a failed run reports.
"""

import collections.abc as cabc
import dataclasses as dc
import logging
import sys
import typing as typ

import pytest

from lading import config as config_module
from lading.commands import publish, publish_preflight
from lading.commands.publish_cargo_config import CargoEnvironment
from lading.commands.publish_errors import (
    ArchiveMismatchError,
    ExpectedDigestsError,
    PublishedArchiveConflictError,
)
from lading.runtime import redacting_runner as redacting_runner_module
from lading.workspace.metadata import CargoMetadataInvocationError
from tests.helpers.publish_digests import (
    OTHER_ARCHIVE,
    PLAIN_ARCHIVE,
    PublishRig,
    digest,
)

from .index_missing_outputs import INDEX_MISSING_STDERR_BETA

if typ.TYPE_CHECKING:
    from lading.runtime.subprocess_runner import SubprocessContext

_ALREADY_EXISTS = "error: crate alpha@0.1.0 already exists on crates.io index"


def test_each_crate_is_read_from_its_own_target_directory(
    digest_rig: PublishRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale matching archive in another crate's target directory is ignored."""
    digest_rig.cargo.crate_targets = {"alpha": "t-alpha", "beta": "t-beta"}
    digest_rig.cargo.packaged["beta"] = OTHER_ARCHIVE
    digest_rig.cargo.stray_archives["alpha"] = {"beta-0.1.0.crate": PLAIN_ARCHIVE}

    with pytest.raises(ArchiveMismatchError, match=r"beta 0\.1\.0 differs"):
        digest_rig.run(monkeypatch, live=True, expect_digests=digest_rig.digests_file())


def test_both_cargo_commands_are_pinned_to_crates_io(
    digest_rig: PublishRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cargo's own configuration cannot redirect a digest-checked run."""
    digest_rig.run(monkeypatch, live=True, expect_digests=digest_rig.digests_file())

    commands = [
        *digest_rig.cargo.subcommands("package"),
        *digest_rig.cargo.subcommands("publish"),
    ]
    assert commands, "the run must have invoked cargo"
    for command in commands:
        pair = command[command.index("--registry") : command.index("--registry") + 2]
        assert pair == ("--registry", "crates-io"), f"{command} must name crates-io"


def test_no_registry_flag_without_the_option(
    digest_rig: PublishRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without digests, Cargo keeps choosing its own registry."""
    digest_rig.run(monkeypatch, live=True)

    assert all("--registry" not in command for command, _ in digest_rig.cargo.calls), (
        "the registry pin is part of the option, not of every run"
    )


def test_identical_published_version_is_skipped_even_if_local_packaging_differs(
    digest_rig: PublishRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The published bytes decide, so a differing local build is never made."""
    digest_rig.registry.published["alpha"] = PLAIN_ARCHIVE
    digest_rig.cargo.packaged["alpha"] = OTHER_ARCHIVE

    summary = digest_rig.run(
        monkeypatch, live=True, expect_digests=digest_rig.digests_file()
    )

    assert "alpha" not in digest_rig.cargo.crates_run("package"), (
        "an identical published version must not be packaged"
    )
    assert "- alpha 0.1.0: already published and identical" in summary.replace(
        " (skipping cargo publish)", ""
    ), "the summary must report it as already published"


def test_different_published_archive_is_a_conflict_not_a_local_mismatch(
    digest_rig: PublishRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A taken version fails as a conflict even when the local build also differs."""
    digest_rig.registry.published["alpha"] = OTHER_ARCHIVE
    digest_rig.cargo.packaged["alpha"] = OTHER_ARCHIVE

    with pytest.raises(PublishedArchiveConflictError):
        digest_rig.run(monkeypatch, live=True, expect_digests=digest_rig.digests_file())

    assert digest_rig.cargo.calls == [], "nothing may be packaged or published"


def test_dry_run_skips_a_published_version_in_both_phases(
    digest_rig: PublishRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dry run neither packages nor dry-run-publishes an identical version."""
    digest_rig.registry.published["beta"] = PLAIN_ARCHIVE

    digest_rig.run(monkeypatch, expect_digests=digest_rig.digests_file())

    assert digest_rig.cargo.crates_run("package") == ["alpha", "gamma"], (
        "the published crate must not be packaged"
    )
    assert digest_rig.cargo.crates_run("publish") == ["alpha", "gamma"], (
        "the published crate must not be dry-run published"
    )
    assert digest_rig.registry.requests == [
        ("alpha", "0.1.0"),
        ("beta", "0.1.0"),
        ("gamma", "0.1.0"),
    ], "each version is looked up once"


@pytest.mark.parametrize(
    ("entries", "expected_text"),
    [
        ({"alpha": PLAIN_ARCHIVE, "beta": PLAIN_ARCHIVE}, "no entry for gamma"),
        (
            dict.fromkeys(("alpha", "beta", "gamma", "delta"), PLAIN_ARCHIVE),
            "delta-0.1.0.crate, which is not in the plan",
        ),
    ],
    ids=["missing_entry", "extra_entry"],
)
def test_plan_disagreement_is_found_before_the_preflight_runs(
    digest_rig: PublishRig,
    monkeypatch: pytest.MonkeyPatch,
    entries: dict[str, bytes],
    expected_text: str,
) -> None:
    """The pre-flight's auxiliary builds can package, so it must come second."""
    preflight_calls: list[object] = []

    with pytest.raises(ExpectedDigestsError, match=expected_text):
        digest_rig.run(
            monkeypatch,
            preflight=lambda *args, **_kwargs: preflight_calls.append(args),
            live=True,
            expect_digests=digest_rig.digests_file(entries),
        )

    assert not preflight_calls, "the pre-flight must not have run"


def test_pre_flight_still_runs_once_the_digests_are_valid(
    digest_rig: PublishRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validating early does not skip the pre-flight."""
    preflight_calls: list[object] = []

    digest_rig.run(
        monkeypatch,
        preflight=lambda *args, **_kwargs: preflight_calls.append(args),
        expect_digests=digest_rig.digests_file(),
    )

    assert len(preflight_calls) == 1, "the pre-flight must run exactly once"


def test_failed_run_still_reports_the_archive_checks(
    digest_rig: PublishRig,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Outcomes decided before a failure are not lost with the summary."""
    caplog.set_level(logging.ERROR)
    digest_rig.cargo.packaged["beta"] = OTHER_ARCHIVE

    with pytest.raises(ArchiveMismatchError, match=r"beta 0\.1\.0 differs"):
        digest_rig.run(monkeypatch, live=True, expect_digests=digest_rig.digests_file())

    report = next(
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("Archive checks against")
    )
    assert "- alpha 0.1.0: verified" in report, "earlier crates are reported"
    assert "- beta 0.1.0: mismatch" in report, "the failing crate is reported"


def test_identical_published_check_does_not_overwrite_unverified(
    digest_rig: PublishRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dry-run crate that could not be packaged stays unverified."""
    # beta is not published when first looked up, then Cargo reports it exists.
    beta_answers = iter([None, digest(PLAIN_ARCHIVE)])
    digest_rig.cargo.failing["package", "beta"] = (101, INDEX_MISSING_STDERR_BETA)
    digest_rig.cargo.failing["publish", "beta"] = (101, _ALREADY_EXISTS)

    def fetch(name: str, _version: str) -> str | None:
        """Answer beta from the script and everything else as unpublished."""
        return next(beta_answers) if name == "beta" else None

    summary = digest_rig.run(
        monkeypatch,
        expect_digests=digest_rig.digests_file(),
        allow_unpublished_workspace_deps=True,
        published_digest_fetcher=fetch,
    )

    assert "- beta 0.1.0: unverified" in summary, (
        "an identical published archive must not turn unverified into verified"
    )


def test_dry_run_publish_archive_is_checked_against_the_expectation(
    digest_rig: PublishRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``cargo publish --dry-run`` packages again, so its archive is compared."""
    digest_rig.cargo.uploaded["beta"] = OTHER_ARCHIVE

    with pytest.raises(ArchiveMismatchError, match=r"dry-run.*beta 0\.1\.0 differs"):
        digest_rig.run(monkeypatch, expect_digests=digest_rig.digests_file())

    assert digest_rig.cargo.crates_run("package") == ["alpha", "beta", "gamma"], (
        "packaging matched, so the dry run reaches the publish phase"
    )
    assert digest_rig.cargo.crates_run("publish") == ["alpha", "beta"], (
        "gamma's dry-run publish must not run after beta's mismatch"
    )


def test_unfetched_workspace_is_discovered_locked_when_digests_are_expected(
    digest_rig: PublishRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Programmatic discovery inside ``run`` honours the locked mode too."""
    seen: list[dict[str, object]] = []

    def discover(_root: object, **kwargs: object) -> object:
        """Record how discovery was asked for and return the rig's workspace."""
        seen.append(kwargs)
        return digest_rig.workspace()

    monkeypatch.setattr("lading.workspace.load_workspace", discover)
    monkeypatch.setattr(
        publish_preflight, "_run_preflight_checks", lambda *_a, **_k: None
    )

    def run(**option_fields: object) -> None:
        """Run publish without supplying a workspace graph."""
        options = dc.replace(
            publish.PublishOptions(
                build_directory=digest_rig.build_directory,
                command_runner=digest_rig.cargo,
                published_digest_fetcher=digest_rig.registry,
                cargo_environment=CargoEnvironment(None, None),
            ),
            **option_fields,
        )
        publish.run(
            digest_rig.root,
            config_module.LadingConfig(
                publish=config_module.PublishConfig(strip_patches="all")
            ),
            None,
            options=options,
        )

    run(expect_digests=digest_rig.digests_file())
    run()

    assert seen == [{"locked": True}, {}], (
        "discovery must be locked exactly when digests are expected"
    )


def test_programmatic_discovery_runs_through_the_redacting_runner(
    digest_rig: PublishRig,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """With no workspace supplied, ``cargo metadata`` output is redacted too."""
    leak = "metadata-credential-0f1e2d3c4b5a"
    caplog.set_level(logging.DEBUG)

    def child_prints_the_token(
        command: cabc.Sequence[str], context: SubprocessContext
    ) -> tuple[int, str, str]:
        """Run a child that fails printing the token, whatever was asked."""
        del command
        script = f"import sys; print({leak!r}, file=sys.stderr); sys.exit(1)"
        return real_run_logged([sys.executable, "-c", script], context)

    real_run_logged = redacting_runner_module.run_logged
    monkeypatch.setattr(redacting_runner_module, "run_logged", child_prints_the_token)
    monkeypatch.setattr(
        publish_preflight, "_run_preflight_checks", lambda *_a, **_k: None
    )
    options = publish.PublishOptions(
        build_directory=digest_rig.build_directory,
        expect_digests=digest_rig.digests_file(),
        cargo_environment=CargoEnvironment(None, None, (leak,)),
    )

    with pytest.raises(CargoMetadataInvocationError) as excinfo:
        publish.run(
            digest_rig.root,
            config_module.LadingConfig(
                publish=config_module.PublishConfig(strip_patches="all")
            ),
            None,
            options=options,
        )

    assert leak not in str(excinfo.value), "the discovery error must not show it"
    assert "<redacted>" in str(excinfo.value), "the error must show the redaction"
    assert leak not in caplog.text, "no log record may carry it"
