"""Tests for the registries a crate's ``publish`` key names."""

import typing as typ

import pytest

from lading.workspace import build_workspace_graph
from tests.helpers.workspace_metadata import build_test_package, create_test_manifest

if typ.TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("publish", "expected_publish", "expected_registries"),
    [
        (None, True, None),
        ([], False, ()),
        (["crates-io"], True, ("crates-io",)),
        (["internal", "crates-io"], True, ("internal", "crates-io")),
        (["internal"], True, ("internal",)),
    ],
    ids=["unrestricted", "disabled", "crates_io", "both", "elsewhere"],
)
def test_registries_follow_the_publish_key(
    tmp_path: Path,
    publish: list[str] | None,
    *,
    expected_publish: bool,
    expected_registries: tuple[str, ...] | None,
) -> None:
    """``registries`` is ``None`` unless ``publish`` lists registry names."""
    manifest = create_test_manifest(
        tmp_path, "crate", '[package]\nname = "crate"\nversion = "0.1.0"\n'
    )
    metadata = {
        "workspace_root": str(tmp_path),
        "packages": [build_test_package("crate", "0.1.0", manifest, publish=publish)],
        "workspace_members": ["crate-id"],
    }

    crate = build_workspace_graph(metadata).crates_by_name["crate"]

    assert crate.publish is expected_publish, "publish must follow the manifest key"
    assert crate.registries == expected_registries, (
        "registries must follow the manifest key"
    )
