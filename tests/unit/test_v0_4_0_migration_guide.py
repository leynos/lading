"""Validate the marked Python imports in the 0.4.0 migration guide."""

import ast
import importlib
from pathlib import Path

_GUIDE_PATH = Path("docs/v0-4-0-migration-guide.md")


def _marked_python_example(document: str, identifier: str) -> str:
    """Extract one marked Python fence from the migration guide."""
    lines = document.splitlines()
    marker = f"<!-- tested-example: {identifier} -->"
    marker_indexes = [index for index, line in enumerate(lines) if line == marker]
    assert len(marker_indexes) == 1, f"expected one marker for {identifier}"
    marker_index = marker_indexes[0]
    fence_index = marker_index + 2
    assert not lines[marker_index + 1], "the marker must precede the fence"
    assert lines[fence_index] == "```python", "the marked example must be Python"
    closing_fence = next(
        (
            index
            for index in range(fence_index + 1, len(lines))
            if lines[index] == "```"
        ),
        None,
    )
    assert closing_fence is not None, "the marked Python fence must close"
    return "\n".join(lines[fence_index + 1 : closing_fence])


def _marked_from_import(document: str, identifier: str) -> tuple[str, tuple[str, ...]]:
    """Parse a marked example containing one from-import statement."""
    source = _marked_python_example(document, identifier)
    statements = ast.parse(source).body
    assert len(statements) == 1, f"expected one import in {identifier}"
    statement = statements[0]
    assert isinstance(statement, ast.ImportFrom), (
        f"expected a from-import in {identifier}"
    )
    assert statement.module is not None, f"expected a module in {identifier}"
    return statement.module, tuple(alias.name for alias in statement.names)


def test_before_import_example_is_expected_to_fail_on_0_4_0() -> None:
    """The historical import must no longer resolve after the helper move."""
    document = _GUIDE_PATH.read_text(encoding="utf-8")
    module_name, names = _marked_from_import(document, "v0-4-0-helper-import-before")
    module = importlib.import_module(module_name)
    missing_names = tuple(name for name in names if not hasattr(module, name))

    assert missing_names == names, (
        "the historical from-import must raise ImportError on 0.4.0"
    )


def test_after_import_example_resolves_on_0_4_0() -> None:
    """The replacement helper import must remain available."""
    document = _GUIDE_PATH.read_text(encoding="utf-8")
    module_name, names = _marked_from_import(document, "v0-4-0-helper-import-after")
    module = importlib.import_module(module_name)

    for name in names:
        assert callable(getattr(module, name, None)), (
            f"the current import example must expose {name}"
        )
