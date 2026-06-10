"""Integration-focused unit tests for the :mod:`lading.commands.bump` module."""

from __future__ import annotations

import dataclasses as dc
import pathlib
import tempfile
import typing as typ
from unittest import mock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from tomlkit import items as tk_items
from tomlkit import parse as parse_toml

from lading import config as config_module
from lading.commands import bump
from lading.workspace import WorkspaceDependency, WorkspaceGraph
from tests.helpers.workspace_builders import (
    _build_workspace_with_internal_deps,
    _CrateSpec,
    _create_alpha_crate,
    _create_beta_crate_with_dependencies,
    _load_version,
    _make_config,
    _make_workspace,
    _write_workspace_manifest,
)

if typ.TYPE_CHECKING:
    from _pytest.monkeypatch import MonkeyPatch


@dc.dataclass(frozen=True, slots=True)
class _NoChangeScenario:
    """Parameters describing expected output when no manifests change."""

    test_id: str
    dry_run: bool
    expected_message: str


@dc.dataclass(frozen=True, slots=True)
class _ReadmeTransposeScenario:
    """Parameters for README transposition tests."""

    test_id: str
    exclude: tuple[str, ...] = ()
    check_version_unchanged: bool = False


@dc.dataclass(frozen=True, slots=True)
class _LockfileSkipScenario:
    """Parameters describing lockfile rebuild skip scenarios."""

    test_id: str
    version: str
    rebuild_lockfiles: bool
    fail_message: str
    expected_message: str | None


@pytest.fixture(autouse=True)
def stub_lockfile_regeneration(monkeypatch: MonkeyPatch) -> None:
    """Avoid invoking Cargo from manifest-focused bump tests."""
    monkeypatch.setattr(
        bump.bump_lockfiles,
        "regenerate_lockfiles",
        lambda *_args, **_kwargs: (),
    )


def _extract_alpha_dependency_entries(
    manifest_path: pathlib.Path,
) -> tuple[str, object, object]:
    """Return the alpha dependency entries across manifest sections."""
    document = parse_toml(manifest_path.read_text(encoding="utf-8"))
    dependency = document["dependencies"]["alpha"].value
    dev_entry = document["dev-dependencies"]["alpha"]
    build_entry = document["build-dependencies"]["alpha"]
    return dependency, dev_entry, build_entry


def test_run_updates_workspace_and_members(tmp_path: pathlib.Path) -> None:
    """`bump.run` updates the workspace and member manifest versions."""
    workspace = _make_workspace(tmp_path)
    configuration = _make_config()
    options = bump.BumpOptions(configuration=configuration, workspace=workspace)
    message = bump.run(tmp_path, "1.2.3", options=options)
    assert message.splitlines() == [
        "Updated version to 1.2.3 in 3 manifest(s):",
        "- Cargo.toml",
        "- crates/alpha/Cargo.toml",
        "- crates/beta/Cargo.toml",
    ]
    assert _load_version(tmp_path / "Cargo.toml", ("workspace", "package")) == "1.2.3"
    for crate in workspace.crates:
        assert _load_version(crate.manifest_path, ("package",)) == "1.2.3"


def test_run_updates_root_package_section(tmp_path: pathlib.Path) -> None:
    """The workspace manifest `[package]` section also receives the new version."""
    workspace = _make_workspace(tmp_path)
    manifest_path = tmp_path / "Cargo.toml"
    manifest_path.write_text(
        "[package]\n"
        'name = "workspace"\n'
        'version = "0.1.0"\n\n'
        "[workspace]\n"
        'members = ["crates/alpha", "crates/beta"]\n\n'
        "[workspace.package]\n"
        'version = "0.1.0"\n'
    )
    configuration = _make_config()
    bump.run(
        tmp_path,
        "7.8.9",
        options=bump.BumpOptions(configuration=configuration, workspace=workspace),
    )
    assert _load_version(manifest_path, ("package",)) == "7.8.9"
    assert _load_version(manifest_path, ("workspace", "package")) == "7.8.9"


def test_run_skips_excluded_crates(tmp_path: pathlib.Path) -> None:
    """Crates listed in `bump.exclude` retain their original version."""
    workspace = _make_workspace(tmp_path)
    excluded = workspace.crates[0]
    configuration = _make_config(exclude=(excluded.name,))
    bump.run(
        tmp_path,
        "2.0.0",
        options=bump.BumpOptions(configuration=configuration, workspace=workspace),
    )
    assert _load_version(tmp_path / "Cargo.toml", ("workspace", "package")) == "2.0.0"
    assert _load_version(excluded.manifest_path, ("package",)) == "0.1.0"
    included = workspace.crates[1]
    assert _load_version(included.manifest_path, ("package",)) == "2.0.0"


def test_run_updates_internal_dependency_versions(tmp_path: pathlib.Path) -> None:
    """Internal dependency requirements are updated across dependency sections."""
    alpha_crate = _create_alpha_crate(tmp_path)
    beta_crate = _create_beta_crate_with_dependencies(tmp_path, alpha_crate.id)
    _write_workspace_manifest(
        tmp_path,
        [
            "crates/alpha",
            "crates/beta",
        ],
    )
    workspace = WorkspaceGraph(
        workspace_root=tmp_path, crates=(alpha_crate, beta_crate)
    )

    configuration = _make_config()
    bump.run(
        tmp_path,
        "1.2.3",
        options=bump.BumpOptions(configuration=configuration, workspace=workspace),
    )

    dependency_version, dev_entry, build_entry = _extract_alpha_dependency_entries(
        beta_crate.manifest_path
    )
    assert dependency_version == "^1.2.3"
    assert dev_entry["version"].value == "~1.2.3"
    assert dev_entry["path"].value == "../alpha"
    assert build_entry["version"].value == "1.2.3"
    assert build_entry["path"].value == "../alpha"


def test_run_updates_documentation_snippets(tmp_path: pathlib.Path) -> None:
    """Documentation TOML fences are rewritten to reference the new version."""
    workspace = _make_workspace(tmp_path)
    readme_path = tmp_path / "README.md"
    readme_path.write_text(
        """# Sample\n\n```toml\n[dependencies]\nalpha = \"0.1.0\"\n```\n""",
        encoding="utf-8",
    )
    configuration = _make_config(documentation_globs=("README.md",))

    message = bump.run(
        tmp_path,
        "1.2.3",
        options=bump.BumpOptions(configuration=configuration, workspace=workspace),
    )

    assert "documentation file(s)" in message
    assert "- README.md (documentation)" in message.splitlines()
    updated_readme = readme_path.read_text(encoding="utf-8")
    assert 'alpha = "1.2.3"' in updated_readme


@pytest.mark.parametrize(
    "scenario",
    [
        _ReadmeTransposeScenario(
            test_id="included_crate",
        ),
        _ReadmeTransposeScenario(
            test_id="excluded_crate",
            exclude=("alpha",),
            check_version_unchanged=True,
        ),
    ],
    ids=lambda s: s.test_id,
)
def test_run_transposes_workspace_readme_to_crates(
    tmp_path: pathlib.Path, scenario: _ReadmeTransposeScenario
) -> None:
    """README adoption follows crate opt-in regardless of version-bump exclusion."""
    workspace, _manifests = _build_workspace_with_internal_deps(
        tmp_path,
        specs=(_CrateSpec(name="alpha", readme_workspace=True),),
    )
    (tmp_path / "README.md").write_text(
        "# Sample\n\nSee [Guide](docs/guide.md).\n",
        encoding="utf-8",
    )
    configuration = _make_config(exclude=scenario.exclude)

    message = bump.run(
        tmp_path,
        "1.2.3",
        options=bump.BumpOptions(configuration=configuration, workspace=workspace),
    )

    crate_readme = tmp_path / "crates" / "alpha" / "README.md"
    assert "readme file(s)" in message
    assert "- crates/alpha/README.md (readme)" in message.splitlines()
    assert crate_readme.read_text(encoding="utf-8") == (
        "# Sample\n\nSee [Guide](../../docs/guide.md).\n"
    )
    if scenario.check_version_unchanged:
        assert (
            _load_version(tmp_path / "crates" / "alpha" / "Cargo.toml", ("package",))
            == "0.1.0"
        )


def test_run_rebuilds_lockfiles_by_default(
    tmp_path: pathlib.Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Verify regenerate_lockfiles calls.

    Lockfile regeneration is called when enabled and suppressed when disabled.
    """
    workspace = _make_workspace(tmp_path)
    configuration = _make_config()
    nested_lockfile = tmp_path / "crates/ui/Cargo.lock"
    captured: dict[str, object] = {}

    def fake_regenerate_lockfiles(
        workspace_root: pathlib.Path,
        lockfile_manifests: tuple[str, ...],
        *,
        runner: object | None = None,
    ) -> tuple[pathlib.Path, ...]:
        captured["calls"] = int(captured.get("calls", 0)) + 1
        captured["workspace_root"] = workspace_root
        captured["lockfile_manifests"] = lockfile_manifests
        captured["runner"] = runner
        return (tmp_path / "Cargo.lock", nested_lockfile)

    monkeypatch.setattr(
        bump.bump_lockfiles,
        "regenerate_lockfiles",
        fake_regenerate_lockfiles,
    )

    message = bump.run(
        tmp_path,
        "1.2.3",
        options=bump.BumpOptions(
            rebuild_lockfiles=True,
            configuration=configuration,
            workspace=workspace,
        ),
    )

    assert captured == {
        "calls": 1,
        "workspace_root": tmp_path,
        "lockfile_manifests": (),
        "runner": None,
    }
    assert "2 lockfile(s)" in message
    assert "- Cargo.lock (lockfile)" in message.splitlines()
    assert "- crates/ui/Cargo.lock (lockfile)" in message.splitlines()

    disabled_root = tmp_path / "disabled"
    disabled_workspace = _make_workspace(disabled_root)
    disabled_configuration = _make_config()
    monkeypatch.setattr(
        bump.bump_lockfiles,
        "regenerate_lockfiles",
        lambda *args, **kwargs: pytest.fail(
            "regenerate_lockfiles must not be called when rebuild_lockfiles=False"
        ),
    )

    message = bump.run(
        disabled_root,
        "1.2.3",
        options=bump.BumpOptions(
            rebuild_lockfiles=False,
            configuration=disabled_configuration,
            workspace=disabled_workspace,
        ),
    )

    assert "lockfile" not in message


def test_run_inherits_lockfile_rebuild_configuration(
    tmp_path: pathlib.Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Programmatic bump calls inherit lockfile rebuild configuration by default."""
    workspace = _make_workspace(tmp_path)
    configuration = config_module.LadingConfig(
        bump=config_module.BumpConfig(rebuild_lockfiles=False)
    )

    def fail_regeneration(*args: object, **kwargs: object) -> typ.NoReturn:
        pytest.fail("lockfile regeneration should inherit configuration")

    monkeypatch.setattr(
        bump.bump_lockfiles,
        "regenerate_lockfiles",
        fail_regeneration,
    )

    message = bump.run(
        tmp_path,
        "1.2.3",
        options=bump.BumpOptions(configuration=configuration, workspace=workspace),
    )

    assert "lockfile" not in message


def test_run_reports_lockfiles_in_dry_run(
    tmp_path: pathlib.Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Dry-run bump output reports lockfiles without regenerating them."""
    workspace = _make_workspace(tmp_path)
    configuration = config_module.LadingConfig(
        bump=config_module.BumpConfig(lockfile_manifests=("crates/ui/Cargo.toml",))
    )
    nested_lockfile = tmp_path / "crates/ui/Cargo.lock"
    captured: dict[str, object] = {}

    def fake_resolve_lockfile_paths(
        workspace_root: pathlib.Path,
        lockfile_manifests: tuple[str, ...],
    ) -> tuple[pathlib.Path, ...]:
        captured["workspace_root"] = workspace_root
        captured["lockfile_manifests"] = lockfile_manifests
        return (tmp_path / "Cargo.lock", nested_lockfile)

    def fail_regeneration(*args: object, **kwargs: object) -> typ.NoReturn:
        pytest.fail("dry-run lockfile reporting should not invoke Cargo")

    monkeypatch.setattr(
        bump.bump_lockfiles,
        "resolve_lockfile_paths",
        fake_resolve_lockfile_paths,
    )
    monkeypatch.setattr(
        bump.bump_lockfiles,
        "regenerate_lockfiles",
        fail_regeneration,
    )

    message = bump.run(
        tmp_path,
        "1.2.3",
        options=bump.BumpOptions(
            dry_run=True,
            rebuild_lockfiles=True,
            configuration=configuration,
            workspace=workspace,
        ),
    )

    assert captured == {
        "workspace_root": tmp_path,
        "lockfile_manifests": ("crates/ui/Cargo.toml",),
    }
    assert "2 lockfile(s)" in message
    assert "- Cargo.lock (lockfile)" in message.splitlines()
    assert "- crates/ui/Cargo.lock (lockfile)" in message.splitlines()


def test_run_updates_renamed_internal_dependency_versions(
    tmp_path: pathlib.Path,
) -> None:
    """Aliased workspace dependencies are updated using their manifest name."""
    workspace, manifests = _build_workspace_with_internal_deps(
        tmp_path,
        specs=(
            _CrateSpec(name="alpha"),
            _CrateSpec(
                name="beta",
                manifest_extra="""
                [dependencies]
                alpha-core = { package = "alpha", version = "^0.1.0" }
                """,
                dependencies=(
                    WorkspaceDependency(
                        package_id="alpha-id",
                        name="alpha",
                        manifest_name="alpha-core",
                        kind=None,
                    ),
                ),
            ),
        ),
    )

    configuration = _make_config()
    bump.run(
        tmp_path,
        "2.3.4",
        options=bump.BumpOptions(configuration=configuration, workspace=workspace),
    )

    beta_manifest = manifests["beta"]
    beta_document = parse_toml(beta_manifest.read_text(encoding="utf-8"))
    dependency_entry = beta_document["dependencies"]["alpha-core"]
    assert dependency_entry["version"].value == "^2.3.4"
    assert dependency_entry["package"].value == "alpha"


def test_run_normalises_workspace_root(
    tmp_path: pathlib.Path, monkeypatch: MonkeyPatch
) -> None:
    """The command resolves the workspace root before applying updates."""
    workspace_root = tmp_path / "workspace-root"
    workspace = _make_workspace(workspace_root)
    configuration = _make_config()
    relative = pathlib.Path("workspace-root")
    monkeypatch.chdir(tmp_path)
    bump.run(
        relative,
        "3.4.5",
        options=bump.BumpOptions(configuration=configuration, workspace=workspace),
    )
    manifest_path = workspace_root / "Cargo.toml"
    assert _load_version(manifest_path, ("workspace", "package")) == "3.4.5"


def test_run_uses_loaded_configuration_and_workspace(
    tmp_path: pathlib.Path, monkeypatch: MonkeyPatch
) -> None:
    """`bump.run` loads the configuration and workspace when omitted."""
    workspace = _make_workspace(tmp_path)
    configuration = _make_config()
    monkeypatch.setattr(config_module, "current_configuration", lambda: configuration)
    monkeypatch.setattr("lading.workspace.load_workspace", lambda root: workspace)
    bump.run(tmp_path, "9.9.9")
    assert _load_version(tmp_path / "Cargo.toml", ("workspace", "package")) == "9.9.9"


@pytest.mark.parametrize(
    "scenario",
    [
        _NoChangeScenario(
            test_id="live",
            dry_run=False,
            expected_message=(
                "No manifest changes required; all versions already 0.1.0."
            ),
        ),
        _NoChangeScenario(
            test_id="dry-run",
            dry_run=True,
            expected_message=(
                "Dry run; no manifest changes required; all versions already 0.1.0."
            ),
        ),
    ],
    ids=lambda scenario: scenario.test_id,
)
def test_run_reports_when_versions_already_match(
    tmp_path: pathlib.Path, scenario: _NoChangeScenario, monkeypatch: MonkeyPatch
) -> None:
    """Report the no-op message for both live and dry-run invocations."""
    workspace = _make_workspace(tmp_path)
    configuration = _make_config()

    def fail_regeneration(*args: object, **kwargs: object) -> typ.NoReturn:
        pytest.fail("no-op bumps must not regenerate lockfiles")

    monkeypatch.setattr(
        bump.bump_lockfiles,
        "regenerate_lockfiles",
        fail_regeneration,
    )
    message = bump.run(
        tmp_path,
        "0.1.0",
        options=bump.BumpOptions(
            dry_run=scenario.dry_run,
            configuration=configuration,
            workspace=workspace,
        ),
    )
    assert message == scenario.expected_message


@pytest.mark.parametrize(
    "scenario",
    [
        _LockfileSkipScenario(
            test_id="disabled",
            version="1.2.3",
            rebuild_lockfiles=False,
            fail_message="lockfile regeneration should be skipped",
            expected_message=None,
        ),
        _LockfileSkipScenario(
            test_id="versions_already_match",
            version="0.1.0",
            rebuild_lockfiles=True,
            fail_message=(
                "lockfiles should not be regenerated without manifest changes"
            ),
            expected_message=(
                "No manifest changes required; all versions already 0.1.0."
            ),
        ),
    ],
    ids=lambda scenario: scenario.test_id,
)
def test_run_skips_lockfile_rebuild(
    tmp_path: pathlib.Path,
    monkeypatch: MonkeyPatch,
    scenario: _LockfileSkipScenario,
) -> None:
    """Lockfile regeneration is skipped when disabled or no manifests changed."""
    workspace = _make_workspace(tmp_path)
    configuration = _make_config()

    def fail_regeneration(*args: object, **kwargs: object) -> typ.NoReturn:
        pytest.fail(scenario.fail_message)

    monkeypatch.setattr(
        bump.bump_lockfiles,
        "regenerate_lockfiles",
        fail_regeneration,
    )

    message = bump.run(
        tmp_path,
        scenario.version,
        options=bump.BumpOptions(
            rebuild_lockfiles=scenario.rebuild_lockfiles,
            configuration=configuration,
            workspace=workspace,
        ),
    )

    if scenario.expected_message is not None:
        assert message == scenario.expected_message
    else:
        assert "lockfile" not in message


def test_run_dry_run_reports_changes_without_modifying_files(
    tmp_path: pathlib.Path,
) -> None:
    """Dry-running the command reports planned changes without touching manifests."""
    workspace = _make_workspace(tmp_path)
    configuration = _make_config()
    manifest_paths = [
        tmp_path / "Cargo.toml",
        *[crate.manifest_path for crate in workspace.crates],
    ]
    original_contents = {
        path: path.read_text(encoding="utf-8") for path in manifest_paths
    }

    message = bump.run(
        tmp_path,
        "1.2.3",
        options=bump.BumpOptions(
            dry_run=True,
            rebuild_lockfiles=False,
            configuration=configuration,
            workspace=workspace,
        ),
    )

    assert message.splitlines() == [
        "Dry run; would update version to 1.2.3 in 3 manifest(s):",
        "- Cargo.toml",
        "- crates/alpha/Cargo.toml",
        "- crates/beta/Cargo.toml",
    ]
    for path in manifest_paths:
        assert path.read_text(encoding="utf-8") == original_contents[path]


@pytest.mark.parametrize(
    ("section", "versions"),
    [
        ("dependencies", ('"0.1.0"', "1.2.3", "1.2.3")),
        ("dev-dependencies", ('"~0.1.0"', "2.0.0", "~2.0.0")),
        ("build-dependencies", ('{ version = "0.1.0" }', "3.0.0", "3.0.0")),
    ],
    ids=["dependencies", "dev-dependencies", "build-dependencies"],
)
def test_run_updates_workspace_dependency_sections(
    tmp_path: pathlib.Path,
    section: str,
    versions: tuple[str, str, str],
) -> None:
    """Workspace dependency entries in [workspace.<section>] are updated."""
    version_spec, target_version, expected_version = versions
    workspace = _make_workspace(tmp_path)
    manifest_path = tmp_path / "Cargo.toml"
    manifest_path.write_text(
        "[workspace]\n"
        'members = ["crates/alpha", "crates/beta"]\n\n'
        "[workspace.package]\n"
        'version = "0.1.0"\n\n'
        f"[workspace.{section}]\n"
        f"alpha = {version_spec}\n",
        encoding="utf-8",
    )
    configuration = _make_config()
    bump.run(
        tmp_path,
        target_version,
        options=bump.BumpOptions(configuration=configuration, workspace=workspace),
    )

    document = parse_toml(manifest_path.read_text(encoding="utf-8"))
    entry = document["workspace"][section]["alpha"]
    # Handle both string format ("0.1.0") and table format ({ version = "0.1.0" })
    if isinstance(entry, (tk_items.Table, tk_items.InlineTable)):
        actual_version = entry["version"].value
    else:
        actual_version = entry.value
    assert actual_version == expected_version


def test_run_updates_workspace_dependency_prefixes(tmp_path: pathlib.Path) -> None:
    """Workspace dependency requirements preserve prefixes and extra fields."""
    workspace = _make_workspace(tmp_path)
    manifest_path = tmp_path / "Cargo.toml"
    manifest_path.write_text(
        "[workspace]\n"
        'members = ["crates/alpha", "crates/beta"]\n\n'
        "[workspace.package]\n"
        'version = "0.1.0"\n\n'
        "[workspace.dependencies]\n"
        'alpha = "^0.1.0"\n'
        'beta = { version = "~0.1.0", path = "crates/beta" }\n',
        encoding="utf-8",
    )
    configuration = _make_config()
    bump.run(
        tmp_path,
        "1.2.3",
        options=bump.BumpOptions(configuration=configuration, workspace=workspace),
    )

    document = parse_toml(manifest_path.read_text(encoding="utf-8"))
    assert document["workspace"]["dependencies"]["alpha"].value == "^1.2.3"
    beta_entry = document["workspace"]["dependencies"]["beta"]
    assert beta_entry["version"].value == "~1.2.3"
    assert beta_entry["path"].value == "crates/beta"


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
    assert context.base_options.rebuild_lockfiles is expected


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
    assert regenerated is expected


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
    assert regenerated is expected
