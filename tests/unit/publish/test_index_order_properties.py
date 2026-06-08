"""Property tests for publish-order index-missing-version handling.

These tests exercise the projected-availability invariant across generated
publish plans. A missing dependency may be downgraded only when it appears
strictly before the failing crate in `PublishPlan.publishable`; dependencies at
later positions are not yet projected to be available and must stay fatal.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from lading.commands import publish

from .conftest import make_crate


@st.composite
def _publish_order_cases(
    draw: st.DrawFn,
) -> tuple[int, int, int]:
    """Generate ``(crate_count, current_index, missing_index)`` cases."""
    crate_count = draw(st.integers(min_value=2, max_value=8))
    current_index = draw(st.integers(min_value=1, max_value=crate_count - 1))
    missing_index = draw(st.integers(min_value=0, max_value=crate_count - 1))
    if missing_index == current_index:
        missing_index = 0
    return crate_count, current_index, missing_index


@given(case=_publish_order_cases())
@settings(max_examples=80)
def test_index_missing_dependency_requires_prior_publish_order(
    case: tuple[int, int, int],
) -> None:
    """Only earlier publish-plan entries count as projected available."""
    crate_count, current_index, missing_index = case
    with tempfile.TemporaryDirectory() as temporary_directory:
        workspace_root = Path(temporary_directory) / "workspace"
        crates = tuple(
            make_crate(workspace_root, f"crate_{index}") for index in range(crate_count)
        )
        plan = publish.PublishPlan(
            workspace_root=workspace_root,
            publishable=crates,
            skipped_manifest=(),
            skipped_configuration=(),
        )
        current_crate = crates[current_index]
        missing_crate = crates[missing_index]
        invocation = publish._CargoInvocation(
            crate_name=current_crate.name,
            subcommand="package",
            output=(
                1,
                "",
                (
                    "error: failed to prepare local package for uploading\n"
                    "Caused by:\n"
                    "  failed to select a version for the requirement "
                    f'`{missing_crate.name} = "^0.1.0"`\n'
                    "  location searched: crates.io index\n"
                ),
            ),
        )
        options = publish._PublishExecutionOptions(
            live=False,
            allow_dirty=True,
            allow_unpublished_workspace_deps=True,
        )

        if missing_index < current_index:
            publish._handle_index_missing_version(
                invocation,
                plan=plan,
                options=options,
            )
        else:
            with pytest.raises(
                publish.PublishPreflightError,
                match=r"appears after .* in publish order",
            ):
                publish._handle_index_missing_version(
                    invocation,
                    plan=plan,
                    options=options,
                )
