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
    "inline-dependency-section": (
        "[target.'cfg(unix)']\ndependencies = { alpha = \"1.0.0\" }\n"
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
        ('alpha-core = { package = "alpha", version = "1.0.0" }', True, "2.0.0"),
        ('alpha = { package = "serde", version = "1.0.0" }', False, "1.0.0"),
    ],
    ids=["aliased-key", "key-with-foreign-package"],
)
def test_documentation_pass_resolves_package_identity(
    entry: str, *, expected_changed: bool, expected_version: str
) -> None:
    """Fence entries are identified by their ``package`` field, then their key.

    An alias whose package is a bump target is rewritten, while a key that
    names a target but declares a different package is left alone.
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


def test_documentation_pass_rewrites_under_out_of_order_target_parent() -> None:
    """A `target` parent declared after its sub-tables is still traversed."""
    text = _toml_fence(
        "[target.'cfg(unix)'.dependencies]\nalpha = \"1.0.0\"\n[target]\n"
    )

    updated, changed = _rewrite_fence(text)

    assert changed is True, "an out-of-order [target] parent must still be traversed"
    document = parse_toml(_fence_body(updated))
    assert document["target"]["cfg(unix)"]["dependencies"]["alpha"] == "2.0.0", updated


def test_documentation_pass_rewrites_out_of_order_dependency_entry() -> None:
    """An ``OutOfOrderTableProxy`` dependency entry is rewritten in place.

    The shared updater mutates the proxy's ``version`` field, so an entry split
    across several headers receives the target version like any other shape.
    """
    text = _toml_fence(
        "[target.'cfg(unix)'.dependencies.alpha]\n"
        'version = "1.0.0"\n'
        "[target.'cfg(unix)'.dependencies]\n"
        'beta = "1.0.0"\n'
        "[target.'cfg(unix)'.dependencies.alpha.metadata]\n"
        'note = "alpha"\n'
    )

    # Both names in one call: the proxy entry and the plain sibling in the
    # same section must both be rewritten.
    updated, changed = bump_docs.rewrite_markdown_toml_fences(
        text, {"alpha", "beta"}, "2.0.0"
    )

    assert changed is True, "both entries must be rewritten"
    document = parse_toml(_fence_body(updated))
    section = document["target"]["cfg(unix)"]["dependencies"]
    assert section["beta"] == "2.0.0", updated
    assert section["alpha"]["version"] == "2.0.0", (
        f"the out-of-order proxy entry must reach the target version: {updated!r}"
    )


_ALIAS_FENCE = (
    "# Sample\n\n```toml\n[dependencies]\n"
    'alias = { package = "alpha", version = "0.1.0" }\n'
    'alpha = { package = "unrelated", version = "3.0.0" }\n'
    "```\n"
)


@dc.dataclass(frozen=True, slots=True)
class _AliasScenario:
    """Parameters for a documented-alias bump scenario.

    Attributes
    ----------
    test_id : str
        Identifier used in assertion messages and the pytest parameter id.
    exclude : tuple[str, ...]
        Crate names excluded from the bump.
    dry_run : bool
        Whether the bump previews without writing.
    expected_alias : str
        The quoted version the documented alias must record afterwards.
    expected_documents : int
        The documentation-file count the summary must report.
    """

    test_id: str
    exclude: tuple[str, ...] = ()
    dry_run: bool = False
    expected_alias: str = '"1.2.3"'
    expected_documents: int = 1


@pytest.mark.parametrize(
    "scenario",
    [
        _AliasScenario(test_id="alias-updated"),
        _AliasScenario(
            test_id="excluded-crate",
            exclude=("alpha",),
            expected_alias='"0.1.0"',
            expected_documents=0,
        ),
        _AliasScenario(
            test_id="dry-run",
            dry_run=True,
            expected_alias='"0.1.0"',
        ),
    ],
    ids=lambda scenario: scenario.test_id,
)
def test_run_resolves_documented_aliases(
    tmp_path: pathlib.Path,
    snapshot: SnapshotAssertion,
    scenario: _AliasScenario,
) -> None:
    """Documentation identity follows ``package``, ``bump.exclude``, and dry runs."""
    workspace = _make_workspace(tmp_path)
    readme_path = tmp_path / "README.md"
    written_readme = _ALIAS_FENCE
    readme_path.write_text(written_readme, encoding="utf-8")
    configuration = _make_config(
        exclude=scenario.exclude,
        documentation_globs=("README.md",),
    )
    options = bump.BumpOptions(configuration=configuration, workspace=workspace)
    if scenario.dry_run:
        options = dc.replace(options, dry_run=True)

    message = bump.run(tmp_path, "1.2.3", options=options)

    updated_readme = readme_path.read_text(encoding="utf-8")
    expected_alias = scenario.expected_alias
    assert f'alias = {{ package = "alpha", version = {expected_alias} }}' in (
        updated_readme
    ), (
        f"{scenario.test_id}: the documented alias must resolve through its "
        "package field"
    )
    assert 'alpha = { package = "unrelated", version = "3.0.0" }' in updated_readme, (
        f"{scenario.test_id}: a key that renames another package must stay untouched"
    )
    if scenario.dry_run:
        assert updated_readme == written_readme, (
            f"{scenario.test_id}: a dry run must leave the documentation file "
            "unmodified"
        )
        documents = scenario.expected_documents
        assert f"{documents} documentation file(s)" in message, (
            f"{scenario.test_id}: a dry run must still report the documentation file"
        )
    assert message == snapshot, (
        f"{scenario.test_id}: the bump summary drifted from its recorded snapshot"
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
