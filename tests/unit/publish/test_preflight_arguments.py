"""Unit tests for publish preflight argument helpers."""

from __future__ import annotations

from lading.commands import publish


def _make_options(
    *,
    test_excludes: tuple[str, ...] = (),
    unit_tests_only: bool = False,
) -> publish._CargoPreflightOptions:
    return publish._CargoPreflightOptions(
        extra_args=(),
        test_excludes=test_excludes,
        unit_tests_only=unit_tests_only,
    )


def test_build_test_arguments_does_not_mutate_base_list() -> None:
    """The helper copies base arguments before applying mutations."""
    base = ["--workspace"]
    options = _make_options()

    result = publish._build_test_arguments(base, options)

    assert result is not base
    assert base == ["--workspace"]


def test_build_test_arguments_appends_unit_test_flags_before_excludes() -> None:
    """Unit tests only mode inserts lib/bin flags ahead of exclusions."""
    base = ["--workspace"]
    options = _make_options(
        unit_tests_only=True, test_excludes=("beta", " alpha ", "beta")
    )

    result = publish._build_test_arguments(base, options)

    assert result[:3] == ["--workspace", "--lib", "--bins"]
    assert result[3:] == ["--exclude", "alpha", "--exclude", "beta"]


def test_build_test_arguments_ignores_blank_excludes() -> None:
    """Whitespace-only entries are ignored when building test arguments."""
    base = ["--workspace"]
    options = _make_options(test_excludes=("", " ", "\t"))

    result = publish._build_test_arguments(base, options)

    assert "--exclude" not in result
