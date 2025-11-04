"""Unit tests for the publish-check cargo shim."""

# ruff: noqa: D103

from __future__ import annotations

import importlib.util
import typing as typ
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

if typ.TYPE_CHECKING:
    from types import ModuleType

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "publish-check" / "bin" / "cargo"
)


def load_cargo_shim() -> ModuleType:
    loader = SourceFileLoader("publish_check_cargo_shim", str(SCRIPT_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        msg = f"Failed to load cargo shim from {SCRIPT_PATH!s}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_inserts_flag_before_separator() -> None:
    shim = load_cargo_shim()
    result = shim.rewrite_args(["test", "--", "--test-threads", "1"])
    assert result == ["test", "--all-features", "--", "--test-threads", "1"]


def test_appends_flag_when_no_separator() -> None:
    shim = load_cargo_shim()
    result = shim.rewrite_args(["check"])
    assert result == ["check", "--all-features"]


def test_leaves_empty_arguments_unchanged() -> None:
    shim = load_cargo_shim()
    result = shim.rewrite_args([])
    assert result == []


def test_leaves_only_separator_unchanged() -> None:
    shim = load_cargo_shim()
    result = shim.rewrite_args(["--"])
    assert result == ["--"]


def test_preserves_existing_flag_before_separator() -> None:
    shim = load_cargo_shim()
    args = ["test", "--all-features", "--", "--nocapture"]
    result = shim.rewrite_args(args)
    assert result == args


def test_repositions_flag_after_separator() -> None:
    shim = load_cargo_shim()
    result = shim.rewrite_args(["test", "--", "--test-threads", "1", "--all-features"])
    assert result == ["test", "--all-features", "--", "--test-threads", "1"]


def test_ignores_non_target_commands() -> None:
    shim = load_cargo_shim()
    args = ["run", "--example", "demo"]
    result = shim.rewrite_args(args)
    assert result == args


def test_handles_toolchain_and_global_flags() -> None:
    shim = load_cargo_shim()
    args = ["+nightly", "--locked", "--manifest-path", "demo/Cargo.toml", "test"]
    result = shim.rewrite_args(args)
    assert result == [
        "+nightly",
        "--locked",
        "--manifest-path",
        "demo/Cargo.toml",
        "test",
        "--all-features",
    ]


@pytest.mark.parametrize("subcommand", ["bench", "clippy"])
def test_inserts_flag_for_additional_subcommands(subcommand: str) -> None:
    shim = load_cargo_shim()
    result = shim.rewrite_args([subcommand])
    assert result == [subcommand, "--all-features"]


@pytest.mark.parametrize(
    "flag_and_value",
    [
        ("--target-dir", "ci-target"),
        ("--config", "ci-config.toml"),
    ],
)
def test_handles_global_flags_consuming_values(flag_and_value: tuple[str, str]) -> None:
    shim = load_cargo_shim()
    flag, value = flag_and_value
    args = [flag, value, "test", "--", "--nocapture"]
    result = shim.rewrite_args(args)
    assert result == [flag, value, "test", "--all-features", "--", "--nocapture"]
