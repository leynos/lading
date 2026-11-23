"""Validate executable and output examples in the 0.4.0 migration guide."""

import ast
import importlib
import json
import shlex
import sys
import tomllib
from pathlib import Path

import pytest
from cuprum import ExecutionContext, Program, ProgramCatalogue, scoped, sh

from tests.helpers.markdown_examples import MarkdownExample, load_markdown_examples

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GUIDE_PATH = _REPO_ROOT / "docs" / "v0-4-0-migration-guide.md"
_EXPECTED_EXAMPLE_IDS = (
    "v0-4-0-helper-import-before",
    "v0-4-0-helper-import-after",
    "v0-4-0-dependency-import-before",
    "v0-4-0-dependency-import-after",
    "v0-4-0-requires-python",
    "v0-4-0-python313-wheel-rejection",
    "v0-4-0-upgrade-uv",
    "v0-4-0-upgrade-venv",
    "v0-4-0-pin-before-floor",
)


def _examples_by_id() -> dict[str, MarkdownExample]:
    """Load the published migration guide examples by stable identifier."""
    return {
        example.identifier: example for example in load_markdown_examples(_GUIDE_PATH)
    }


def _imports_for_example(
    example: MarkdownExample,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Parse every import statement in a marked Python example."""
    assert example.language == "python", (
        f"{example.identifier} must remain a Python example"
    )
    imports: list[tuple[str, tuple[str, ...]]] = []
    for statement in ast.parse(example.source).body:
        assert isinstance(statement, ast.ImportFrom), (
            f"{example.identifier} must contain only from-imports"
        )
        assert statement.module is not None, "from-imports must name a module"
        imports.append((
            statement.module,
            tuple(alias.name for alias in statement.names),
        ))
    return tuple(imports)


def _stub_program(directory: Path, name: str) -> Program:
    """Create an allowlisted command that records its arguments."""
    command = directory / name
    command.write_text(
        f"#!{sys.executable}\n"
        "import json\n"
        "import os\n"
        "import sys\n"
        "with open(os.environ['DOC_EXAMPLE_LOG'], 'a', encoding='utf-8') as log:\n"
        "    log.write(\n"
        "        json.dumps([os.path.basename(sys.argv[0]), *sys.argv[1:]])\n"
        "        + '\\n'\n"
        "    )\n",
        encoding="utf-8",
    )
    command.chmod(0o755)
    return Program(str(command))


def _parse_shell_commands(example: MarkdownExample) -> list[list[str]]:
    """Parse the guide's simple command lines without invoking a shell."""
    assert example.language == "bash", (
        f"{example.identifier} must remain a Bash example"
    )
    commands = [
        shlex.split(line, comments=True)
        for line in example.source.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    return [command for command in commands if command]


def _exercise_shell_example(
    example: MarkdownExample, tmp_path: Path
) -> list[list[str]]:
    """Run documented commands through Cuprum with an explicit program set."""
    commands = _parse_shell_commands(example)
    stub_directory = tmp_path / "bin"
    stub_directory.mkdir()
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    executable_paths = {
        "uv": stub_directory / "uv",
        "python3.14": stub_directory / "python3.14",
        "python": venv_bin / "python",
    }
    programs = {
        name: _stub_program(path.parent, path.name)
        for name, path in executable_paths.items()
    }
    catalogue = ProgramCatalogue.from_programs(
        *programs.values(), name="migration-guide-examples"
    )
    log_path = tmp_path / "commands.jsonl"
    context = ExecutionContext(cwd=tmp_path, env={"DOC_EXAMPLE_LOG": str(log_path)})
    with scoped(catalogue=catalogue):
        for command in commands:
            program_name = command[0]
            if program_name == ".venv/bin/python":
                program_name = "python"
            program = programs.get(program_name)
            assert program is not None, (
                f"{example.identifier} uses an unapproved executable {command[0]!r}"
            )
            result = sh.make(program, catalogue=catalogue)(*command[1:]).run_sync(
                context=context
            )
            assert result.exit_code == 0, (
                f"{example.identifier} command failed: {command!r}: {result.stderr}"
            )
    return [
        json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()
    ]


def test_migration_guide_loads_every_marked_example() -> None:
    """The published guide must mark every fence with a unique stable ID."""
    examples = load_markdown_examples(_GUIDE_PATH)

    assert tuple(example.identifier for example in examples) == _EXPECTED_EXAMPLE_IDS, (
        "the migration guide must keep its complete stable example set"
    )


@pytest.mark.parametrize(
    ("example_id", "missing_name"),
    [
        ("v0-4-0-helper-import-before", "parse_manifest"),
        ("v0-4-0-dependency-import-before", "DEPENDENCY_SECTIONS"),
    ],
)
def test_before_import_examples_fail_on_0_4_0(
    example_id: str, missing_name: str
) -> None:
    """The historical helper imports must no longer resolve on 0.4.0."""
    for module_name, names in _imports_for_example(_examples_by_id()[example_id]):
        module = importlib.import_module(module_name)
        missing_names = tuple(name for name in names if not hasattr(module, name))
        assert missing_names == names, (
            f"the historical import must fail for {module_name}"
        )
        assert missing_name in missing_names, (
            f"the example must include moved helper {missing_name}"
        )


@pytest.mark.parametrize(
    "example_id",
    ["v0-4-0-helper-import-after", "v0-4-0-dependency-import-after"],
)
def test_after_import_examples_resolve_on_0_4_0(example_id: str) -> None:
    """The replacement helper imports must resolve and keep their public shape."""
    for module_name, names in _imports_for_example(_examples_by_id()[example_id]):
        module = importlib.import_module(module_name)
        for name in names:
            assert hasattr(module, name), f"{module_name} must expose {name}"
            if name != "DEPENDENCY_SECTIONS":
                assert callable(getattr(module, name)), f"{name} must be callable"


def test_requires_python_example_matches_project_metadata() -> None:
    """The documented new interpreter floor must match package metadata."""
    example = _examples_by_id()["v0-4-0-requires-python"]
    project_metadata = tomllib.loads(
        (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]
    requirements = project_metadata["requires-python"]

    assert "# 0.3.1\nRequires-Python: >=3.13" in example.source, (
        "the historical metadata example must retain its Python 3.13 floor"
    )
    assert f"# 0.4.0\nRequires-Python: {requirements}" in example.source, (
        "the release metadata example must match the current requirement"
    )
    assert requirements == ">=3.14", (
        "the project must declare the documented Python 3.14 floor"
    )


def test_python313_rejection_example_states_the_package_constraint() -> None:
    """The installer transcript must agree with the current Python floor."""
    example = _examples_by_id()["v0-4-0-python313-wheel-rejection"]
    requirements = tomllib.loads(
        (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["requires-python"]

    assert "$ python3.13 -m pip install lading-0.4.0-py3-none-any.whl" in (
        example.source
    ), "the installation example must use Python 3.13 and the 0.4.0 wheel"
    assert "3.13.x not in '>=3.14'" in example.source, (
        "the pip example must explain rejection against the >=3.14 requirement"
    )
    assert requirements == ">=3.14", (
        "the example's rejection constraint must match project metadata"
    )


@pytest.mark.parametrize(
    ("example_id", "expected_calls"),
    [
        (
            "v0-4-0-upgrade-uv",
            [
                ["uv", "python", "install", "3.14"],
                ["uv", "python", "pin", "3.14"],
                ["uv", "add", "lading==0.4.0"],
            ],
        ),
        (
            "v0-4-0-upgrade-venv",
            [
                ["python3.14", "-m", "venv", ".venv"],
                ["python", "-m", "pip", "install", "lading==0.4.0"],
            ],
        ),
        ("v0-4-0-pin-before-floor", [["uv", "add", "lading<0.4.0"]]),
    ],
)
def test_upgrade_shell_examples_run_the_documented_commands(
    example_id: str,
    expected_calls: list[list[str]],
    tmp_path: Path,
) -> None:
    """Run each shell example against stubs and check its command sequence."""
    calls = _exercise_shell_example(_examples_by_id()[example_id], tmp_path)

    assert calls == expected_calls, (
        f"{example_id} must run the documented command sequence"
    )
