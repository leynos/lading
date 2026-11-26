"""Tests for finding the registry Cargo's configuration makes the default."""

import typing as typ
from pathlib import Path

import pytest

from lading.commands import publish
from lading.commands.publish_cargo_config import (
    CargoEnvironment,
    effective_default_registry,
)
from lading.commands.publish_errors import ExpectedDigestsError

if typ.TYPE_CHECKING:
    from tests.helpers.publish_digests import PublishRig


def _write_config(directory: Path, text: str, *, name: str = "config.toml") -> Path:
    """Write ``text`` to ``directory/.cargo/<name>`` and return the file."""
    target = directory / ".cargo" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def _default(registry: str) -> str:
    """Return a Cargo configuration whose default registry is ``registry``."""
    return f'[registry]\ndefault = "{registry}"\n'


def test_nothing_configured_means_no_default(tmp_path: Path) -> None:
    """An empty tree and no Cargo home leave Cargo on crates.io."""
    assert (
        effective_default_registry(tmp_path, cargo_home=None, env_default=None) is None
    ), "no configuration must give no default"


def test_nearest_configuration_wins(tmp_path: Path) -> None:
    """The workspace's own config beats an ancestor's, which beats Cargo home."""
    root = tmp_path / "outer" / "ws"
    root.mkdir(parents=True)
    _write_config(tmp_path, _default("ancestor"))
    home = tmp_path / "home"
    (home).mkdir()
    (home / "config.toml").write_text(_default("home"), encoding="utf-8")

    assert effective_default_registry(root, cargo_home=home, env_default=None) == (
        "ancestor"
    ), "an ancestor must beat Cargo home"
    _write_config(root, _default("workspace"))
    assert effective_default_registry(root, cargo_home=home, env_default=None) == (
        "workspace"
    ), "the workspace root must beat its ancestors"


def test_environment_beats_every_file(tmp_path: Path) -> None:
    """``CARGO_REGISTRY_DEFAULT`` takes precedence over configuration."""
    _write_config(tmp_path, _default("configured"))

    assert (
        effective_default_registry(tmp_path, cargo_home=None, env_default="from-env")
        == "from-env"
    ), "the environment must win"


def test_legacy_config_name_is_read(tmp_path: Path) -> None:
    """The legacy ``.cargo/config`` is honoured as Cargo does."""
    _write_config(tmp_path, _default("legacy"), name="config")

    assert effective_default_registry(tmp_path, cargo_home=None, env_default=None) == (
        "legacy"
    ), "the legacy file name must be read"


def test_file_without_a_default_defers_to_the_next(tmp_path: Path) -> None:
    """A config that sets other things does not mask a farther default."""
    root = tmp_path / "ws"
    root.mkdir()
    _write_config(root, "[build]\njobs = 2\n")
    _write_config(tmp_path, _default("ancestor"))

    assert effective_default_registry(root, cargo_home=None, env_default=None) == (
        "ancestor"
    ), "only a file that sets registry.default decides"


def test_malformed_configuration_is_reported(tmp_path: Path) -> None:
    """A config Cargo could not read is an error, not a guess."""
    _write_config(tmp_path, "[registry\n")

    with pytest.raises(ExpectedDigestsError, match="Cannot read Cargo configuration"):
        effective_default_registry(tmp_path, cargo_home=None, env_default=None)


@pytest.mark.parametrize("where", ["workspace", "ancestor", "cargo_home"])
def test_configured_alternate_default_is_refused_before_anything_runs(
    digest_rig: PublishRig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    where: str,
) -> None:
    """A Cargo-configured alternate default stops the run before staging."""
    cargo_home = tmp_path / "cargo-home"
    cargo_home.mkdir()
    match where:
        case "workspace":
            _write_config(digest_rig.root, _default("internal"))
        case "ancestor":
            _write_config(digest_rig.root.parent, _default("internal"))
        case _:
            (cargo_home / "config.toml").write_text(
                _default("internal"), encoding="utf-8"
            )

    with pytest.raises(ExpectedDigestsError, match=r"crates\.io only.*'internal'"):
        digest_rig.run(
            monkeypatch,
            live=True,
            expect_digests=digest_rig.digests_file(),
            environment=CargoEnvironment(None, cargo_home),
        )

    assert not digest_rig.cargo.calls, "nothing may be packaged or published"
    assert not digest_rig.build_directory.exists(), "nothing may be staged"


def test_configured_crates_io_default_proceeds(
    digest_rig: PublishRig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Naming crates.io as the configured default is not another registry."""
    _write_config(digest_rig.root, _default("crates-io"))

    digest_rig.run(
        monkeypatch,
        live=True,
        expect_digests=digest_rig.digests_file(),
        environment=CargoEnvironment(None, tmp_path / "absent-cargo-home"),
    )

    assert digest_rig.cargo.crates_run("publish") == ["alpha", "beta", "gamma"], (
        "the run must proceed"
    )


def test_member_level_alternate_default_is_refused(
    digest_rig: PublishRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A member crate's own configuration can set a default, so it is checked."""
    _write_config(digest_rig.root / "beta", _default("internal"))

    with pytest.raises(ExpectedDigestsError, match=r"crate beta.*'internal'"):
        digest_rig.run(monkeypatch, live=True, expect_digests=digest_rig.digests_file())

    assert not digest_rig.cargo.calls, "nothing may be packaged or published"
    assert not digest_rig.build_directory.exists(), "nothing may be staged"


def test_extensionless_config_wins_when_both_files_exist(tmp_path: Path) -> None:
    """Cargo reads only ``config`` when ``config.toml`` sits beside it."""
    _write_config(tmp_path, _default("internal"), name="config")
    _write_config(tmp_path, _default("crates-io"), name="config.toml")

    assert effective_default_registry(tmp_path, cargo_home=None, env_default=None) == (
        "internal"
    ), "the extensionless file must be the one that is read"


def test_toml_config_is_ignored_when_the_extensionless_file_exists(
    tmp_path: Path,
) -> None:
    """An alternate default in the shadowed ``config.toml`` does not apply."""
    _write_config(tmp_path, "[build]\njobs = 1\n", name="config")
    _write_config(tmp_path, _default("internal"), name="config.toml")

    assert (
        effective_default_registry(tmp_path, cargo_home=None, env_default=None) is None
    ), "Cargo would not read the shadowed file, so neither does lading"


def test_digests_without_a_resolved_cargo_environment_are_refused(
    digest_rig: PublishRig,
) -> None:
    """A programmatic caller must say what Cargo's environment is."""
    options = publish.PublishOptions(
        build_directory=digest_rig.build_directory,
        command_runner=digest_rig.cargo,
        expect_digests=digest_rig.digests_file(),
    )

    with pytest.raises(ExpectedDigestsError, match="cargo_environment"):
        publish.run(digest_rig.root, None, digest_rig.workspace(), options=options)

    assert not digest_rig.cargo.calls, "nothing may run on a guess"
    assert not digest_rig.build_directory.exists(), "nothing may be staged"
