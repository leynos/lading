"""Property tests for publish preflight failure transitions."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from lading.commands import (
    publish,
    publish_pipeline,
    publish_preflight,
    publish_staging,
)
from lading.commands.publish_errors import PublishPreflightError

from .conftest import make_config, make_crate, make_workspace

_FAILURE_DETAIL = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789 ",
    min_size=1,
    max_size=24,
)


@given(detail=_FAILURE_DETAIL)
@settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_run_never_stages_or_dispatches_after_preflight_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, detail: str
) -> None:
    """Any preflight error prevents the staging and publication phases."""
    root = tmp_path / "workspace"
    workspace = make_workspace(root, make_crate(root, "alpha"))
    configuration = make_config()
    reached_phases: list[str] = []

    def fail_preflight(*_args: object, **_kwargs: object) -> None:
        raise PublishPreflightError(detail)

    def record_staging(*_args: object, **_kwargs: object) -> None:
        reached_phases.append("staging")

    def record_dispatch(*_args: object, **_kwargs: object) -> None:
        reached_phases.append("dispatch")

    monkeypatch.setattr(publish_preflight, "_run_preflight_checks", fail_preflight)
    monkeypatch.setattr(publish_staging, "prepare_workspace", record_staging)
    monkeypatch.setattr(publish_pipeline, "_dispatch_publication", record_dispatch)

    with pytest.raises(PublishPreflightError, match=detail):
        publish.run(root, configuration, workspace)

    assert reached_phases == []
