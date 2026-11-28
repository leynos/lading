"""Unit tests for the ``PlanBuilder.propose`` write-remains contract."""

from pathlib import Path

from lading.commands.bump_plan import WriteCategory
from lading.commands.bump_plan_builder import PlanBuilder
from lading.commands.bump_preview import DocumentationPreview
from lading.utils.confinement import ConfinedPath, ConfineOptions, confine_relative_path

_ORIGINAL = '[dependencies]\nalpha = "0.1.0"\n'
_PROPOSED = '[dependencies]\nalpha = "1.2.3"\n'
_FINAL = '[dependencies]\nalpha = "1.3.0"\n'


def _builder(tmp_path: Path) -> tuple[PlanBuilder, ConfinedPath]:
    """Return a builder and the confined README beneath it."""
    root = tmp_path.resolve()
    (root / "README.md").write_text(_ORIGINAL, encoding="utf-8")
    confined = confine_relative_path(
        root, "README.md", key="test", options=ConfineOptions(may_create=True)
    )
    return PlanBuilder(root=root), confined


def test_propose_reports_that_a_write_remains(tmp_path: Path) -> None:
    """A proposal that differs from the observed text leaves a write."""
    builder, confined = _builder(tmp_path)

    recorded = builder.propose(
        confined,
        _PROPOSED,
        WriteCategory.DOCUMENTATION,
        observed=_ORIGINAL,
    )

    assert recorded is True, "a changed proposal must report a remaining write"
    plan = builder.build(target_version="1.2.3", lockfiles=None)
    assert any(proposal.path.path == confined.path for proposal in plan.proposals), (
        "the plan must hold the documentation write"
    )


def test_propose_reports_when_the_observed_text_is_already_current(
    tmp_path: Path,
) -> None:
    """An observed snapshot equal to the proposal records no write."""
    builder, confined = _builder(tmp_path)

    recorded = builder.propose(
        confined,
        _PROPOSED,
        WriteCategory.DOCUMENTATION,
        observed=_PROPOSED,
    )

    assert recorded is False, "an unchanged file must report no remaining write"
    plan = builder.build(target_version="1.2.3", lockfiles=None)
    assert not plan.proposals, "an unchanged file must not be written"


def test_propose_reports_when_a_restore_removes_the_write(tmp_path: Path) -> None:
    """Restoring the pre-bump text removes the write and reports so."""
    builder, confined = _builder(tmp_path)
    assert builder.propose(confined, _PROPOSED, WriteCategory.DOCUMENTATION), (
        "the first proposal must record a write"
    )

    restored = builder.propose(confined, _ORIGINAL, WriteCategory.DOCUMENTATION)

    assert restored is False, "a restored file must report no remaining write"
    plan = builder.build(target_version="1.2.3", lockfiles=None)
    assert not plan.proposals, "the restored file must not be written"


def test_restore_removes_a_preview_by_filesystem_identity(tmp_path: Path) -> None:
    """A differently spelled path to the same file still drops its preview."""
    builder, confined = _builder(tmp_path)
    root = tmp_path.resolve()
    alias = ConfinedPath(root=root, parts=("ALIAS.md",))
    (root / "ALIAS.md").hardlink_to(root / "README.md")
    builder.record_documentation_preview(
        confined,
        DocumentationPreview(
            path=confined.path, original=_ORIGINAL, proposed=_PROPOSED
        ),
    )
    assert builder.propose(
        confined, _PROPOSED, WriteCategory.DOCUMENTATION, observed=_ORIGINAL
    ), "the proposal must record a write"

    restored = builder.propose(alias, _ORIGINAL, WriteCategory.DOCUMENTATION)

    assert restored is False, "the alias names the same file, so the write is gone"
    plan = builder.build(target_version="1.2.3", lockfiles=None)
    assert not plan.document_previews, (
        "preview removal must use the write's identity, not its spelling"
    )


def test_replacement_updates_the_recorded_preview(tmp_path: Path) -> None:
    """A later write for the same file updates the preview's proposed text."""
    builder, confined = _builder(tmp_path)
    builder.record_documentation_preview(
        confined,
        DocumentationPreview(path=confined.path, original=_ORIGINAL, proposed=_FINAL),
    )
    assert builder.propose(
        confined, _FINAL, WriteCategory.DOCUMENTATION, observed=_ORIGINAL
    ), "the documentation proposal must record a write"

    replacement = '[dependencies]\nalpha = "2.0.0"\n'
    assert builder.propose(confined, replacement, WriteCategory.README), (
        "the replacement must keep a write"
    )

    plan = builder.build(target_version="2.0.0", lockfiles=None)
    (preview,) = plan.document_previews
    assert preview.proposed == replacement, (
        "the preview must describe the text the plan writes"
    )
