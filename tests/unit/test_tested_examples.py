"""Contract tests for the ``tested-example`` documentation loader."""

from pathlib import Path

import pytest

from tests.helpers.markdown_docs import write_document
from tests.helpers.tested_examples import load_tested_examples


def test_loader_reads_a_marked_example(tmp_path: Path) -> None:
    """A valid marker yields its fence body and language."""
    path = write_document(
        tmp_path,
        "<!-- tested-example: example-one -->\n```bash\necho one\n```\n",
    )

    examples = load_tested_examples(path)

    assert examples["example-one"].language == "bash", (
        "the fence language must be captured"
    )
    assert examples["example-one"].body == "echo one", "the fence body must be captured"


def test_loader_rejects_an_unmarked_fence(tmp_path: Path) -> None:
    """A fence without a marker fails the load."""
    path = write_document(tmp_path, "```bash\necho one\n```\n")

    with pytest.raises(ValueError, match="unmarked fenced example"):
        load_tested_examples(path)


def test_loader_can_skip_unmarked_fences_on_request(tmp_path: Path) -> None:
    """A document being migrated loads its marked examples only."""
    path = write_document(
        tmp_path,
        "```bash\necho one\n```\n\n"
        "<!-- tested-example: migrated -->\n```bash\necho two\n```\n",
    )

    examples = load_tested_examples(path, require_markers=False)

    assert set(examples) == {"migrated"}, (
        "only the marked example should be returned when unmarked fences are skipped"
    )
    assert examples["migrated"].body == "echo two", (
        "the marked example must still be parsed"
    )


def test_loader_rejects_a_malformed_marker(tmp_path: Path) -> None:
    """A marker without an identifier fails the load."""
    path = write_document(
        tmp_path,
        "<!-- tested-example: -->\n```bash\necho one\n```\n",
    )

    with pytest.raises(ValueError, match="malformed tested-example marker"):
        load_tested_examples(path)


def test_loader_rejects_a_duplicate_identifier(tmp_path: Path) -> None:
    """Two examples sharing an identifier fail the load."""
    path = write_document(
        tmp_path,
        "<!-- tested-example: same -->\n```bash\necho one\n```\n\n"
        "<!-- tested-example: same -->\n```bash\necho two\n```\n",
    )

    with pytest.raises(ValueError, match="duplicate tested-example"):
        load_tested_examples(path)


def test_loader_matches_a_longer_closing_fence(tmp_path: Path) -> None:
    """A four-backtick fence closes only on four or more backticks."""
    path = write_document(
        tmp_path,
        "<!-- tested-example: long -->\n"
        "````bash\n"
        "echo ``` inside\n"
        "```\n"
        "still inside\n"
        "````\n",
    )

    example = load_tested_examples(path)["long"]

    assert example.language == "bash", "the info string follows the delimiter run"
    assert example.body == "echo ``` inside\n```\nstill inside", (
        "a standalone shorter backtick line must neither close the fence nor be lost"
    )


def test_inline_code_with_a_backtick_fence_lookalike_is_not_a_fence(
    tmp_path: Path,
) -> None:
    """A paragraph such as ```bash``` is inline code, not an unmarked fence."""
    path = write_document(tmp_path, "Use ```bash``` to run the command.\n")

    assert not load_tested_examples(path), (
        "a backtick run inside an info string must not open a fence"
    )
