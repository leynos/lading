"""Integration tests for documentation updates in :mod:`lading.commands.bump`."""

import dataclasses as dc
import pathlib
import typing as typ

import pytest
from tomlkit import parse as parse_toml

from lading.commands import bump, bump_docs, bump_pipeline
from lading.commands.bump_readme import ReadmeTranspositionError
from tests.helpers.path_normalization import normalized
from tests.helpers.workspace_builders import (
    _build_workspace_with_internal_deps,
    _CrateSpec,
    _load_version,
    _make_config,
    _make_workspace,
)

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion


@dc.dataclass(frozen=True, slots=True)
class _ReadmeTransposeScenario:
    """Parameters for README transposition tests."""

    test_id: str
    exclude: tuple[str, ...] = ()
    check_version_unchanged: bool = False


def _toml_fence(snippet: str) -> str:
    """Return ``snippet`` wrapped in a fenced TOML code block."""
    return f"```toml\n{snippet}```\n"


def _fence_body(text: str) -> str:
    """Return the TOML body of a single fence produced by :func:`_toml_fence`."""
    return text.removeprefix("```toml\n").removesuffix("```\n")


def _rewrite_fence(text: str) -> tuple[str, bool]:
    """Rewrite fenced TOML for the ``alpha`` crate to version ``2.0.0``."""
    return bump_docs.rewrite_markdown_toml_fences(text, {"alpha"}, "2.0.0")


def _assert_alpha_rewritten(text: str) -> None:
    """Assert the documentation pass rewrites ``alpha`` inside ``text``."""
    updated, changed = _rewrite_fence(text)
    assert changed is True, f"the documentation pass must rewrite {text!r}"
    assert 'alpha = "2.0.0"' in updated, updated


def test_documentation_pass_rewrites_workspace_and_target_tables() -> None:
    """The two dependency locations reported in issue #370 are rewritten."""
    _assert_alpha_rewritten(_toml_fence('[workspace.dependencies]\nalpha = "1.0.0"\n'))
    _assert_alpha_rewritten(
        _toml_fence("[target.'cfg(unix)'.dependencies]\nalpha = \"1.0.0\"\n")
    )


@pytest.mark.parametrize(
    "section", ["dependencies", "dev-dependencies", "build-dependencies"]
)
def test_documentation_pass_rewrites_workspace_sections(section: str) -> None:
    """Every canonical section is rewritten under ``[workspace]``."""
    _assert_alpha_rewritten(_toml_fence(f'[workspace.{section}]\nalpha = "1.0.0"\n'))


@pytest.mark.parametrize(
    "selector",
    ["'cfg(unix)'", '"cfg(windows)"', "x86_64-unknown-linux-gnu"],
    ids=["single-quoted-cfg", "double-quoted-cfg", "bare-triple"],
)
@pytest.mark.parametrize(
    "section", ["dependencies", "dev-dependencies", "build-dependencies"]
)
def test_documentation_pass_rewrites_target_selector_variants(
    selector: str, section: str
) -> None:
    """Target-specific tables are rewritten for every selector and section."""
    _assert_alpha_rewritten(
        _toml_fence(f'[target.{selector}.{section}]\nalpha = "1.0.0"\n')
    )


def test_readme_transposition_failure_propagates_from_preparation(
    tmp_path: pathlib.Path,
) -> None:
    """A README failure propagates unchanged for the CLI to diagnose."""
    workspace, _manifests = _build_workspace_with_internal_deps(
        tmp_path,
        specs=(_CrateSpec(name="alpha", readme_workspace=True),),
    )
    configuration = _make_config()
    context = bump._initialize_bump_context(
        tmp_path,
        bump.BumpOptions(configuration=configuration, workspace=workspace),
    )

    with pytest.raises(ReadmeTranspositionError) as excinfo:
        bump_pipeline._prepare_plan(context, "1.2.3")

    assert "Workspace README.md is required" in str(excinfo.value), (
        "preparation must propagate the actionable README diagnostic"
    )


def test_run_updates_documentation_snippets(
    tmp_path: pathlib.Path, snapshot: SnapshotAssertion
) -> None:
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

    assert message == snapshot, "the bump summary drifted from its recorded snapshot"
    updated_readme = readme_path.read_text(encoding="utf-8")
    assert 'alpha = "1.2.3"' in updated_readme, (
        f"expected README.md rewritten to the new version: {updated_readme!r}"
    )


def test_run_rewrites_workspace_and_target_fences(
    tmp_path: pathlib.Path, snapshot: SnapshotAssertion
) -> None:
    """A bump rewrites workspace and target fences and reports the file once."""
    workspace = _make_workspace(tmp_path)
    readme_path = tmp_path / "README.md"
    readme_path.write_text(
        "# Sample\n\n"
        '```toml\n[workspace.dependencies]\nalpha = "0.1.0"\n```\n\n'
        "```toml\n[target.'cfg(unix)'.dependencies]\nalpha = \"0.1.0\"\n```\n",
        encoding="utf-8",
    )
    configuration = _make_config(documentation_globs=("README.md",))

    message = bump.run(
        tmp_path,
        "1.2.3",
        options=bump.BumpOptions(configuration=configuration, workspace=workspace),
    )

    assert message == snapshot, "the bump summary drifted from its recorded snapshot"
    updated_readme = readme_path.read_text(encoding="utf-8")
    assert updated_readme.count('alpha = "1.2.3"') == 2, (
        f"expected both fences rewritten to the new version: {updated_readme!r}"
    )
    assert "- README.md (documentation)" in message, (
        f"the summary must report the documentation path once: {message!r}"
    )


# Fence shapes a bump rewrite cannot reach. Each is skipped silently, exactly
# as the manifest pass skips tables it does not model, so the snippet is left
# byte-for-byte unchanged and no change is reported.
_UNSUPPORTED_FENCE_SNIPPETS = {
    "inline-target": (
        "target = { 'cfg(unix)' = { dependencies = { alpha = \"1.0.0\" } } }\n"
    ),
    "scalar-target": 'target = "nonsense"\n',
    "inline-selector": (
        "[target]\n'cfg(unix)' = { dependencies = { alpha = \"1.0.0\" } }\n"
    ),
    "dotted-nested-selector": (
        "[target.'cfg(unix)'.nested.dependencies]\nalpha = \"1.0.0\"\n"
    ),
    "malformed-toml": "[target.'cfg(unix)'.dependencies\nalpha = \"1.0.0\"\n",
}


@pytest.mark.parametrize(
    "snippet", _UNSUPPORTED_FENCE_SNIPPETS.values(), ids=_UNSUPPORTED_FENCE_SNIPPETS
)
def test_documentation_pass_skips_unsupported_target_shapes(snippet: str) -> None:
    """Target shapes outside the traversal's model leave the fence unchanged."""
    text = _toml_fence(snippet)

    updated, changed = _rewrite_fence(text)

    assert changed is False, f"an unsupported shape must not report a change: {text!r}"
    assert updated == text, "an unsupported shape must leave the fence unchanged"


def test_documentation_pass_rewrites_supported_locations_beside_unsupported() -> None:
    """An unreachable location does not stop a supported one in the same fence."""
    text = _toml_fence('target = "nonsense"\n[dependencies]\nalpha = "1.0.0"\n')

    updated, changed = _rewrite_fence(text)

    assert changed is True, "the supported top-level section must still be rewritten"
    assert parse_toml(_fence_body(updated))["dependencies"]["alpha"] == "2.0.0", updated


@pytest.mark.parametrize(
    ("entry", "expected_changed", "expected_version"),
    [
        ('alpha-core = { package = "alpha", version = "1.0.0" }', False, "1.0.0"),
        ('alpha = { package = "serde", version = "1.0.0" }', True, "2.0.0"),
    ],
    ids=["aliased-key", "workspace-key-with-foreign-package"],
)
def test_documentation_pass_matches_fence_keys_literally(
    entry: str, *, expected_changed: bool, expected_version: str
) -> None:
    """Fence keys are matched as written; aliases are not resolved.

    Documented limit: the documentation pass is given crate names and has no
    Cargo metadata, so it cannot map a crate to an aliased manifest key. The
    manifest pass resolves that mapping from the workspace graph before calling
    the same updater, so both passes match keys literally; only the manifest
    pass knows which key an alias denotes.
    """
    text = _toml_fence(f"[target.'cfg(unix)'.dependencies]\n{entry}\n")

    updated, changed = _rewrite_fence(text)

    assert changed is expected_changed, updated
    document = parse_toml(_fence_body(updated))
    table = document["target"]["cfg(unix)"]["dependencies"]
    assert table[entry.split(" = ")[0]]["version"] == expected_version, updated


def test_documentation_pass_is_idempotent() -> None:
    """A second pass over already-rewritten text reports no further change."""
    text = _toml_fence(
        '[workspace.dependencies]\nalpha = "1.0.0"\n\n'
        "[target.'cfg(unix)'.dev-dependencies]\nalpha = \"1.0.0\"\n"
    )
    once, changed = _rewrite_fence(text)
    assert changed is True, "the first pass must rewrite the canonical locations"

    twice, changed_again = _rewrite_fence(once)

    assert changed_again is False, "the second pass must find nothing left to change"
    assert twice == once, "the second pass must return the text it was given"


def test_documentation_pass_visits_every_selector_and_keeps_decoys() -> None:
    """Several selectors are all rewritten while decoy crates stay untouched."""
    text = _toml_fence(
        "[target.x86_64-unknown-linux-gnu.dependencies]\n"
        'alpha = "1.0.0"\nother = "0.9.0"\n\n'
        "[target.'cfg(unix)'.dependencies]\n"
        'alpha = "^1.0.0"\n\n'
        "[target.'cfg(unix)'.decoy-dependencies]\n"
        'alpha = "1.0.0"\n'
    )

    updated, changed = _rewrite_fence(text)

    assert changed is True, "the traversal must rewrite every target selector"
    document = parse_toml(_fence_body(updated))
    bare_target = document["target"]["x86_64-unknown-linux-gnu"]["dependencies"]
    assert bare_target["alpha"] == "2.0.0", "the bare triple selector must be rewritten"
    assert bare_target["other"] == "0.9.0", "non-target crates must not be rewritten"
    cfg_selector = document["target"]["cfg(unix)"]
    assert cfg_selector["dependencies"]["alpha"] == "^2.0.0", (
        "the operator prefix must survive"
    )
    assert cfg_selector["decoy-dependencies"]["alpha"] == "1.0.0", (
        "a decoy section under a selector must not be rewritten"
    )


def test_documentation_pass_skips_out_of_order_dependency_entry() -> None:
    """An ``OutOfOrderTableProxy`` dependency entry keeps its recorded version.

    Documented limitation: the shared updater recognizes ``Table`` and
    ``InlineTable`` entries only, so an entry split across several headers is
    skipped. The same skip applies to manifest rewriting, so the two passes
    still agree; this test records the behaviour rather than endorsing it.
    """
    text = _toml_fence(
        "[target.'cfg(unix)'.dependencies.alpha]\n"
        'version = "1.0.0"\n'
        "[target.'cfg(unix)'.dependencies]\n"
        'beta = "1.0.0"\n'
        "[target.'cfg(unix)'.dependencies.alpha.package]\n"
        'name = "alpha"\n'
    )

    # Both names in one call: the proxy entry is skipped while the plain
    # sibling in the same section is rewritten, so a pass that reached nothing
    # could not report a change.
    updated, changed = bump_docs.rewrite_markdown_toml_fences(
        text, {"alpha", "beta"}, "2.0.0"
    )

    assert changed is True, "the plain sibling entry must be rewritten"
    document = parse_toml(_fence_body(updated))
    section = document["target"]["cfg(unix)"]["dependencies"]
    assert section["beta"] == "2.0.0", updated
    assert section["alpha"]["version"] == "1.0.0", (
        f"the out-of-order proxy entry must keep its version: {updated!r}"
    )


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
    tmp_path: pathlib.Path,
    scenario: _ReadmeTransposeScenario,
    snapshot: SnapshotAssertion,
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
    assert message == snapshot, "the bump summary drifted from its recorded snapshot"
    scrubbed = normalized(
        crate_readme.read_text(encoding="utf-8"),
        "../../",
        placeholder="<link-prefix>",
    )
    assert scrubbed == snapshot(name="crate_readme"), (
        "expected crate README to rewrite relative links to the crate location"
    )
    if scenario.check_version_unchanged:
        assert (
            _load_version(tmp_path / "crates" / "alpha" / "Cargo.toml", ("package",))
            == "0.1.0"
        ), "expected crate version to remain 0.1.0 when bump excludes the crate"
