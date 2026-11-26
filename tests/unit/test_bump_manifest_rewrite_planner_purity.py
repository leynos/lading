"""Keep rewrite planning independent of the filesystem."""

import collections.abc as cabc
import contextlib
import pathlib
import typing as typ
from unittest import mock

import pytest

from lading.commands import bump_manifest_rewrites
from tests.helpers.manifest_rewrites import manifest_rewrite_versions


class _FilesystemForbiddenPath(pathlib.PurePath):
    """Path value that rejects direct filesystem conversion."""

    def __fspath__(self) -> str:
        """Reject attempts to pass this path to an operating-system API."""
        message = "The rewrite planner attempted filesystem access"
        raise AssertionError(message)


@contextlib.contextmanager
def _reject_concrete_path_access(path: pathlib.PurePath) -> cabc.Generator[None]:
    """Reject pathlib checks or opens that target ``path`` during the scope."""
    target_parts = path.parts
    with contextlib.ExitStack() as patches:
        for method_name in ("exists", "stat", "open"):
            original_method: cabc.Callable[..., object] = getattr(
                pathlib.Path, method_name
            )

            def probe(
                candidate: pathlib.Path,
                *args: object,
                _original: cabc.Callable[..., object] = original_method,
                **kwargs: object,
            ) -> object:
                """Reject target access and pass unrelated paths through."""
                if candidate.parts == target_parts:
                    message = "The planner probed the supplied manifest path"
                    raise AssertionError(message)
                return _original(candidate, *args, **kwargs)

            patches.enter_context(
                mock.patch.object(pathlib.Path, method_name, new=probe)
            )
        yield


def test_planner_rewrites_supplied_text_without_filesystem_access(
    tmp_path: pathlib.Path,
) -> None:
    """Planning preserves the supplied path and never probes or opens it."""
    unopened_path = typ.cast(
        "pathlib.Path", _FilesystemForbiddenPath(tmp_path, "unopened/Cargo.toml")
    )
    source = bump_manifest_rewrites.ManifestRewriteSource(
        path=unopened_path,
        content='[dependencies]\nalpha = "1.0.0"\n',
        dependencies=True,
        string_values=(),
    )

    with _reject_concrete_path_access(unopened_path):
        (plan,) = bump_manifest_rewrites.plan_manifest_rewrites(
            (source,), manifest_rewrite_versions()
        )
        with pytest.raises(AssertionError, match="probed the supplied manifest"):
            pathlib.Path(source.path).exists()

    assert plan.path is unopened_path, (
        "the planner should preserve the supplied source identity"
    )
    assert plan.new_text == '[dependencies]\nalpha = "2.0.0"\n', (
        "the planner should use only supplied text and version metadata"
    )
