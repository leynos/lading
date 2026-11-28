"""Dry-run and registry-guard tests for ``lading publish --expect-digests``."""

import typing as typ

import msgspec
import pytest

from lading.commands import publish_digests
from lading.commands.publish_cargo_config import CargoEnvironment
from lading.commands.publish_errors import ArchiveMismatchError, ExpectedDigestsError
from lading.commands.publish_plan import plan_publication
from tests.helpers.publish_digests import (
    OTHER_ARCHIVE,
    PublishRig,
    digest,
)

from .conftest import make_config
from .index_missing_outputs import INDEX_MISSING_STDERR_BETA

if typ.TYPE_CHECKING:
    from lading.workspace import WorkspaceGraph


def test_dry_run_checks_every_packaged_archive_and_runs_locked(
    digest_rig: PublishRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every archive is checked, and the dry-run publish also runs ``--locked``."""
    summary = digest_rig.run(monkeypatch, expect_digests=digest_rig.digests_file())

    for name in ("alpha", "beta", "gamma"):
        assert f"- {name} 0.1.0: verified" in summary, f"{name} must be checked"
    dry_runs = digest_rig.cargo.subcommands("publish")
    assert (
        dry_runs
        == [
            (
                "cargo",
                "publish",
                "--allow-dirty",
                "--locked",
                "--registry",
                "crates-io",
                "--dry-run",
            )
        ]
        * 3
    ), "the dry-run publish gains --locked and --registry crates-io"
    assert digest_rig.registry.requests == [
        ("alpha", "0.1.0"),
        ("beta", "0.1.0"),
        ("gamma", "0.1.0"),
    ], "published versions are looked up before each dry-run publish"


def test_dry_run_mismatch_stops_before_any_publish_dry_run(
    digest_rig: PublishRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The batched packaging phase fails before the publish phase begins."""
    digest_rig.cargo.packaged["gamma"] = OTHER_ARCHIVE

    with pytest.raises(ArchiveMismatchError, match=digest(OTHER_ARCHIVE)):
        digest_rig.run(monkeypatch, expect_digests=digest_rig.digests_file())

    assert digest_rig.cargo.subcommands("publish") == [], "no publish step may have run"


def test_downgraded_packaging_is_reported_unverified_never_verified(
    digest_rig: PublishRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crate whose ``cargo package`` was downgraded has nothing to compare."""
    digest_rig.cargo.failing["package", "beta"] = (101, INDEX_MISSING_STDERR_BETA)

    summary = digest_rig.run(
        monkeypatch,
        expect_digests=digest_rig.digests_file(),
        allow_unpublished_workspace_deps=True,
    )

    assert "- beta 0.1.0: unverified" in summary, (
        "the downgraded crate must be reported unverified"
    )
    assert "- beta 0.1.0: verified" not in summary, (
        "the downgraded crate must never be reported verified"
    )
    assert "- alpha 0.1.0: verified" in summary, "its siblings are still checked"


def test_another_default_registry_is_refused_before_anything_runs(
    digest_rig: PublishRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``CARGO_REGISTRY_DEFAULT`` naming another registry is a clear error."""
    with pytest.raises(ExpectedDigestsError, match=r"crates\.io only.*'internal'"):
        digest_rig.run(
            monkeypatch,
            expect_digests=digest_rig.digests_file(),
            environment=CargoEnvironment("internal", None),
        )

    assert digest_rig.cargo.calls == [], (
        "no cargo command may run for a refused registry"
    )


def test_crates_io_named_explicitly_is_accepted(
    digest_rig: PublishRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Naming the default registry ``crates-io`` is not another registry."""
    digest_rig.run(
        monkeypatch,
        expect_digests=digest_rig.digests_file(),
        environment=CargoEnvironment("crates-io", None),
    )


def _workspace_limiting_beta_to(
    rig: PublishRig, registries: tuple[str, ...] | None
) -> WorkspaceGraph:
    """Return the rig's workspace with ``beta``'s ``publish`` key set."""
    workspace = rig.workspace()
    crates = tuple(
        msgspec.structs.replace(crate, registries=registries)
        if crate.name == "beta"
        else crate
        for crate in workspace.crates
    )
    return msgspec.structs.replace(workspace, crates=crates)


@pytest.mark.parametrize(
    ("registries", "is_refused"),
    [
        (("internal",), True),
        (("crates-io", "internal"), False),
        (("crates-io",), False),
        (None, False),
    ],
    ids=["other_only", "both", "crates_io_only", "unrestricted"],
)
def test_crate_limited_to_another_registry_is_refused(
    digest_rig: PublishRig, registries: tuple[str, ...] | None, *, is_refused: bool
) -> None:
    """A manifest ``publish`` list without crates-io cannot be checked."""
    workspace = _workspace_limiting_beta_to(digest_rig, registries)
    plan = plan_publication(workspace, make_config())

    if is_refused:
        with pytest.raises(
            ExpectedDigestsError, match=r"beta \(publish = \['internal'\]\)"
        ):
            publish_digests.require_crates_io_crates(plan)
    else:
        publish_digests.require_crates_io_crates(plan)


def test_publish_dry_run_downgrade_marks_the_crate_unverified(
    digest_rig: PublishRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A downgraded ``cargo publish --dry-run`` cannot leave a crate verified."""
    digest_rig.cargo.failing["publish", "beta"] = (101, INDEX_MISSING_STDERR_BETA)

    summary = digest_rig.run(
        monkeypatch,
        expect_digests=digest_rig.digests_file(),
        allow_unpublished_workspace_deps=True,
    )

    assert "- beta 0.1.0: unverified" in summary, (
        "the downgraded publish step must be reported unverified"
    )
    assert "- alpha 0.1.0: verified" in summary, "its siblings are unaffected"
