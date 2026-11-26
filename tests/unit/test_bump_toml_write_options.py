"""Exercise atomic-write options and replacement status recording."""

from pathlib import Path

import pytest

from lading.commands import bump_toml_files, bump_toml_write_options


def test_unbounded_write_records_replacement_status(tmp_path: Path) -> None:
    """An unbounded replacement publishes its identity and completion."""
    destination = tmp_path / "Cargo.toml"
    destination.write_text("original", encoding="utf-8")
    status = bump_toml_write_options.AtomicWriteStatus()

    bump_toml_files.write_atomic_text(
        destination,
        "replacement",
        bump_toml_write_options.AtomicWriteOptions(status=status),
    )

    assert status.replacement_completed is True, (
        "a completed path replacement must record completion"
    )
    assert status.replacement_identity is not None, (
        "a path replacement must publish the installed temporary identity"
    )


@pytest.mark.parametrize(
    ("write_options", "message"),
    [
        pytest.param(
            bump_toml_write_options.AtomicWriteOptions(
                expected_current_text="original"
            ),
            "expected_current_text",
            id="text",
        ),
        pytest.param(
            bump_toml_write_options.AtomicWriteOptions(
                expected_current_identity=bump_toml_write_options.AtomicWriteIdentity(
                    1, 2
                )
            ),
            "expected_current_identity",
            id="identity",
        ),
    ],
)
def test_conditional_evidence_requires_a_workspace_boundary(
    tmp_path: Path,
    write_options: bump_toml_write_options.AtomicWriteOptions,
    message: str,
) -> None:
    """Conditional evidence without a workspace boundary is rejected."""
    with pytest.raises(ValueError, match=message):
        bump_toml_files.write_atomic_text(
            tmp_path / "Cargo.toml",
            "replacement",
            write_options,
        )
