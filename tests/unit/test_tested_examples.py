"""Contract tests for the ``tested-example`` documentation loader."""

from pathlib import Path

import pytest

from tests.helpers.tested_examples import load_tested_examples


def _write(tmp_path: Path, text: str) -> Path:
    """Write a small Markdown document and return its path."""
    path = tmp_path / "doc.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_loader_reads_a_marked_example(tmp_path: Path) -> None:
    """A valid marker yields its fence body and language."""
    path = _write(
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
    path = _write(tmp_path, "```bash\necho one\n```\n")

    with pytest.raises(ValueError, match="unmarked fenced example"):
        load_tested_examples(path)


def test_loader_rejects_a_malformed_marker(tmp_path: Path) -> None:
    """A marker without an identifier fails the load."""
    path = _write(
        tmp_path,
        "<!-- tested-example: -->\n```bash\necho one\n```\n",
    )

    with pytest.raises(ValueError, match="malformed tested-example marker"):
        load_tested_examples(path)


def test_loader_rejects_a_duplicate_identifier(tmp_path: Path) -> None:
    """Two examples sharing an identifier fail the load."""
    path = _write(
        tmp_path,
        "<!-- tested-example: same -->\n```bash\necho one\n```\n\n"
        "<!-- tested-example: same -->\n```bash\necho two\n```\n",
    )

    with pytest.raises(ValueError, match="duplicate tested-example"):
        load_tested_examples(path)


def test_loader_rejects_an_unmarked_tilde_fence(tmp_path: Path) -> None:
    """A tilde fence without a marker fails the load."""
    path = _write(tmp_path, "~~~bash\necho one\n~~~\n")

    with pytest.raises(ValueError, match="unmarked fenced example"):
        load_tested_examples(path)


def test_loader_matches_a_longer_closing_fence(tmp_path: Path) -> None:
    """A four-backtick fence closes only on four or more backticks."""
    path = _write(
        tmp_path,
        "<!-- tested-example: long -->\n````bash\necho ``` inside\n````\n",
    )

    example = load_tested_examples(path)["long"]

    assert example.language == "bash", "the info string follows the delimiter run"
    assert "echo ``` inside" in example.body, (
        "a shorter backtick run inside the fence must not close it"
    )
