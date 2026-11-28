"""Unit tests for ``lading.cli`` bump flag forwarding.

Covers the nullable flags the CLI resolves and forwards to ``bump``:
``--dry-run``, ``--rebuild-lockfiles``, and ``--diff``. The ``publish`` flags
are covered by ``test_cli_flags``.
"""

import collections.abc as cabc
import dataclasses as dc
import typing as typ
from pathlib import Path

import pytest

from lading import cli
from lading.commands import bump as bump_command
from lading.commands import bump_lockfiles
from tests.helpers.cli_workspace import make_workspace


@pytest.mark.usefixtures("minimal_config")
def test_bump_cli_accepts_dry_run_flag(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The CLI passes ``dry_run=True`` when the flag is provided."""
    workspace_graph = make_workspace(tmp_path.resolve())
    captured_kwargs: dict[str, typ.Any] = {}

    def fake_run(*args: object, **kwargs: object) -> str:
        """Record the keyword arguments of a dry-run bump and return a preview."""
        captured_kwargs.update(kwargs)
        return "preview"

    monkeypatch.setattr(bump_command, "run", fake_run)
    monkeypatch.setattr(cli, "load_bump_workspace", lambda _: workspace_graph)

    exit_code = cli.main([
        "--workspace-root",
        str(tmp_path),
        "bump",
        "1.2.3",
        "--dry-run",
    ])

    assert exit_code == 0, f"a dry-run bump must exit 0, got {exit_code}"
    options = captured_kwargs["options"]
    assert isinstance(options, bump_command.BumpOptions), (
        "bump.run must receive a BumpOptions instance"
    )
    assert options.dry_run is True, "the --dry-run flag must be forwarded as True"
    repository = options.lockfile_repository
    assert isinstance(repository, bump_lockfiles.CargoLockfileRepository), (
        "the options must carry the cargo lockfile repository"
    )
    assert repository.runner is cli.subprocess_runner, (
        "the repository must use the CLI subprocess runner"
    )


@dc.dataclass(frozen=True, slots=True)
class _RebuildLockfilesCase:
    """One ``[bump]`` configuration, CLI flags, and the value they must yield.

    ``config_body`` is empty where the case tests a flag on its own, so the
    absence of configuration is stated explicitly rather than inferred.
    """

    config_body: str
    extra_args: tuple[str, ...]
    expected: bool | None


_REBUILD_LOCKFILES_CASES = (
    pytest.param(
        _RebuildLockfilesCase(config_body="", extra_args=(), expected=None),
        id="default",
    ),
    # The False here is hydrated by the Cyclopts TOML loader
    # (use_commands_as_keys=True), not by resolution logic in cli.bump.
    pytest.param(
        _RebuildLockfilesCase(
            config_body="[bump]\nrebuild_lockfiles = false\n",
            extra_args=(),
            expected=False,
        ),
        id="configuration-hydrated-by-cyclopts",
    ),
    pytest.param(
        _RebuildLockfilesCase(
            config_body="[bump]\nrebuild_lockfiles = true\n",
            extra_args=(),
            expected=True,
        ),
        id="configuration-hydrated-by-cyclopts-true",
    ),
    pytest.param(
        _RebuildLockfilesCase(
            config_body="[bump]\nrebuild_lockfiles = false\n",
            extra_args=("--rebuild-lockfiles",),
            expected=True,
        ),
        id="explicit-enable",
    ),
    pytest.param(
        _RebuildLockfilesCase(
            config_body="[bump]\nrebuild_lockfiles = false\n",
            extra_args=("--no-rebuild-lockfiles",),
            expected=False,
        ),
        id="explicit-disable",
    ),
)


def _capture_bump_options(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    extra_args: tuple[str, ...],
) -> bump_command.BumpOptions:
    """Run the bump CLI with ``extra_args`` and return the forwarded options."""
    workspace_graph = make_workspace(tmp_path.resolve())
    captured: dict[str, typ.Any] = {}

    def fake_run(*args: object, **kwargs: object) -> str:
        """Record the keyword arguments of a bump and return a marker."""
        captured.update(kwargs)
        return "bumped"

    monkeypatch.setattr(bump_command, "run", fake_run)
    monkeypatch.setattr(cli, "load_bump_workspace", lambda _: workspace_graph)

    exit_code = cli.main([
        "--workspace-root",
        str(tmp_path),
        "bump",
        "1.2.3",
        *extra_args,
    ])

    assert exit_code == 0, f"bump should succeed, got exit code {exit_code}"
    options = captured["options"]
    assert isinstance(options, bump_command.BumpOptions), (
        "bump.run must receive a BumpOptions instance"
    )
    return options


@pytest.mark.parametrize("case", _REBUILD_LOCKFILES_CASES)
def test_bump_cli_forwards_raw_rebuild_lockfiles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    write_config: cabc.Callable[[str], Path],
    case: _RebuildLockfilesCase,
) -> None:
    """The CLI forwards the nullable flag; resolution belongs to the command."""
    write_config(case.config_body)

    options = _capture_bump_options(monkeypatch, tmp_path, case.extra_args)

    assert options.rebuild_lockfiles is case.expected, (
        f"the flag should be forwarded as {case.expected!r}"
    )


@dc.dataclass(frozen=True, slots=True)
class _DiffFlagCase:
    """One CLI invocation and the ``diff`` value it must forward."""

    extra_args: tuple[str, ...]
    expected: bool


@pytest.mark.usefixtures("minimal_config")
@pytest.mark.parametrize(
    "case",
    [
        pytest.param(_DiffFlagCase(extra_args=(), expected=False), id="default"),
        pytest.param(
            _DiffFlagCase(extra_args=("--dry-run", "--diff"), expected=True),
            id="enabled",
        ),
    ],
)
def test_bump_cli_forwards_diff_flag(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: _DiffFlagCase,
) -> None:
    """The CLI forwards ``--diff`` and defaults it to false when absent."""
    options = _capture_bump_options(monkeypatch, tmp_path, case.extra_args)

    assert options.diff is case.expected, (
        f"the flag should resolve to {case.expected!r}"
    )


@pytest.mark.usefixtures("minimal_config")
def test_bump_function_forwards_flag_bundle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A direct ``cli.bump`` caller supplies every flag through ``flags``."""
    workspace_graph = make_workspace(tmp_path.resolve())
    captured_kwargs: dict[str, typ.Any] = {}

    def fake_run(*args: object, **kwargs: object) -> str:
        """Record the keyword arguments of a direct bump call."""
        captured_kwargs.update(kwargs)
        return "summary"

    monkeypatch.setattr(bump_command, "run", fake_run)
    monkeypatch.setattr(cli, "load_bump_workspace", lambda _: workspace_graph)

    result = cli.bump(
        "1.2.3",
        tmp_path,
        flags=cli.BumpFlags(dry_run=True, diff=True, rebuild_lockfiles=False),
    )

    assert result == "summary", "the command result must be returned unchanged"
    options = captured_kwargs["options"]
    assert isinstance(options, bump_command.BumpOptions), (
        "bump.run must receive a BumpOptions instance"
    )
    assert options.dry_run is True, "flags.dry_run must reach the domain options"
    assert options.diff is True, "flags.diff must reach the domain options"
    assert options.rebuild_lockfiles is False, (
        "flags.rebuild_lockfiles must reach the domain options"
    )


def test_bump_context_preserves_diff_option(tmp_path: Path) -> None:
    """The domain re-construction used by the pipeline keeps ``diff``."""
    from tests.helpers.workspace_builders import (
        _build_workspace_with_internal_deps,
        _CrateSpec,
        _make_config,
    )

    workspace, _manifests = _build_workspace_with_internal_deps(
        tmp_path, specs=(_CrateSpec(name="alpha"),)
    )
    context = bump_command._initialize_bump_context(
        tmp_path,
        bump_command.BumpOptions(
            dry_run=True,
            diff=True,
            configuration=_make_config(),
            workspace=workspace,
        ),
    )

    assert context.base_options.diff is True, (
        "the reconstructed options must keep the diff request"
    )
    assert context.base_options.dry_run is True, (
        "the reconstructed options must keep the dry-run request"
    )
