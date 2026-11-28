"""Unit tests for :class:`~lading.commands.publish_target_dir.CargoTargetLocator`."""

import json
import typing as typ
from pathlib import Path

import pytest

from lading.commands.publish_errors import ArchiveUnavailableError
from lading.commands.publish_target_dir import CargoTargetLocator

if typ.TYPE_CHECKING:
    import collections.abc as cabc


class _Cargo:
    """A runner double answering ``cargo metadata`` and counting its calls."""

    def __init__(self, stdout: str = "", exit_code: int = 0) -> None:
        """Answer every call with ``stdout`` unless it is the default."""
        self.stdout = stdout
        self.exit_code = exit_code
        self.cwds: list[Path | None] = []

    def __call__(
        self,
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        """Record the directory, never echo, and answer."""
        del command, env
        assert echo_stdout is False, "the metadata document must not be echoed"
        self.cwds.append(cwd)
        target = {"target_directory": f"/targets/{cwd.name if cwd else 'none'}"}
        return self.exit_code, self.stdout or json.dumps(target), "stderr text"


def test_each_crate_root_is_asked_once_and_remembered() -> None:
    """The lookup is cached per root, and roots do not share answers."""
    cargo = _Cargo()
    locator = CargoTargetLocator(cargo)

    first = locator(Path("/ws/alpha"))
    again = locator(Path("/ws/alpha"))
    other = locator(Path("/ws/beta"))

    assert first == again == Path("/targets/alpha"), "the same root gives one answer"
    assert other == Path("/targets/beta"), "another root has its own target"
    assert cargo.cwds == [Path("/ws/alpha"), Path("/ws/beta")], "one call per root"


@pytest.mark.parametrize(
    "stdout",
    ["not json", "{}", "[]", '{"target_directory": 3}'],
    ids=["not_json", "no_key", "not_object", "not_a_path"],
)
def test_unusable_cargo_metadata_is_reported(stdout: str) -> None:
    """A target directory that cannot be read stops the run with a reason."""
    locator = CargoTargetLocator(_Cargo(stdout, exit_code=101))

    with pytest.raises(ArchiveUnavailableError, match="target directory"):
        locator(Path("/ws/alpha"))
