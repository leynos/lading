"""Tests for ``rebuild_lockfiles`` resolution in :mod:`lading.commands.bump`."""

from __future__ import annotations

import pathlib
import tempfile
from unittest import mock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from lading import config as config_module
from lading.commands import bump
from tests.helpers.workspace_builders import _make_workspace


@pytest.mark.parametrize("configured", [True, False])
@pytest.mark.parametrize("flag", [None, True, False])
def test_initialize_bump_context_resolves_rebuild_lockfiles(
    tmp_path: pathlib.Path,
    *,
    flag: bool | None,
    configured: bool,
) -> None:
    """The command layer owns the None-coalescing of ``rebuild_lockfiles``.

    Issue #106: the CLI forwards the raw nullable flag; the only resolution
    against ``configuration.bump.rebuild_lockfiles`` happens here. This is the
    white-box counterpart to
    :func:`test_run_resolves_rebuild_lockfiles_through_public_api`, asserting the
    resolved field directly on the initialised context.
    """
    workspace = _make_workspace(tmp_path)
    configuration = config_module.LadingConfig(
        bump=config_module.BumpConfig(rebuild_lockfiles=configured)
    )

    context = bump._initialize_bump_context(
        tmp_path,
        bump.BumpOptions(
            rebuild_lockfiles=flag,
            configuration=configuration,
            workspace=workspace,
        ),
    )

    expected = configured if flag is None else flag
    assert context.base_options.rebuild_lockfiles is expected, (
        f"expected resolved rebuild_lockfiles {expected!r} for flag={flag!r}, "
        f"configured={configured!r}"
    )


def _run_bump_capturing_regeneration(
    workspace_root: pathlib.Path,
    *,
    flag: bool | None,
    configured: bool,
) -> bool:
    """Run ``bump.run`` and report whether lockfile regeneration was invoked.

    The resolved ``rebuild_lockfiles`` value is not exposed by ``bump.run``;
    the only observable behavioural effect is whether the manifest changes
    trigger ``regenerate_lockfiles``. Bumping to ``1.2.3`` from the default
    ``0.1.0`` guarantees manifest changes so regeneration depends solely on the
    resolved flag.
    """
    workspace = _make_workspace(workspace_root)
    configuration = config_module.LadingConfig(
        bump=config_module.BumpConfig(rebuild_lockfiles=configured)
    )
    with mock.patch.object(
        bump.bump_lockfiles,
        "regenerate_lockfiles",
        return_value=(),
    ) as regenerate:
        bump.run(
            workspace_root,
            "1.2.3",
            options=bump.BumpOptions(
                rebuild_lockfiles=flag,
                configuration=configuration,
                workspace=workspace,
            ),
        )
    return regenerate.called


@pytest.mark.parametrize("configured", [True, False])
@pytest.mark.parametrize("flag", [None, True, False])
def test_run_resolves_rebuild_lockfiles_through_public_api(
    tmp_path: pathlib.Path,
    *,
    flag: bool | None,
    configured: bool,
) -> None:
    """`bump.run` resolves the nullable flag against configuration end-to-end.

    Issue #106: exercise the public command boundary rather than the private
    ``_initialize_bump_context`` helper. The resolved value is observed through
    whether lockfile regeneration runs.
    """
    regenerated = _run_bump_capturing_regeneration(
        tmp_path,
        flag=flag,
        configured=configured,
    )

    expected = configured if flag is None else flag
    assert regenerated is expected, (
        f"expected regeneration={expected!r} for flag={flag!r}, "
        f"configured={configured!r}"
    )


@given(flag=st.sampled_from([None, True, False]), configured=st.booleans())
@settings(max_examples=30)
def test_run_rebuild_lockfiles_single_source_of_truth(
    *,
    flag: bool | None,
    configured: bool,
) -> None:
    """Lockfile regeneration follows a single resolution rule for all inputs.

    Issue #106 invariant: across ``rebuild_lockfiles`` ∈ {None, True, False}
    and configured ∈ {True, False}, ``bump.run`` regenerates lockfiles iff the
    CLI flag wins when set, otherwise the configuration default applies. A fresh
    temporary workspace is built per example because each run mutates manifests.
    """
    with tempfile.TemporaryDirectory() as temporary_directory:
        workspace_root = pathlib.Path(temporary_directory) / "workspace"
        regenerated = _run_bump_capturing_regeneration(
            workspace_root,
            flag=flag,
            configured=configured,
        )

    expected = configured if flag is None else flag
    assert regenerated is expected, (
        f"single-source-of-truth violated: regeneration={regenerated!r} but "
        f"expected {expected!r} for flag={flag!r}, configured={configured!r}"
    )
