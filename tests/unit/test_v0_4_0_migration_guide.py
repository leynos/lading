"""Validate executable and output examples in the 0.4.0 migration guide."""

import ast
import importlib
import json
import runpy
import shlex
import sys
import tomllib
from pathlib import Path

import pytest
from cuprum import ExecutionContext, Program, ProgramCatalogue, scoped, sh
from markdown_it import MarkdownIt

from lading import config as config_module
from lading.commands import bump_docs
from tests.helpers.markdown_examples import MarkdownExample, load_markdown_examples

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GUIDE_PATH = _REPO_ROOT / "docs" / "v0-4-0-migration-guide.md"
_EXPECTED_EXAMPLE_IDS = (
    "v0-4-0-upgrade-uv",
    "v0-4-0-upgrade-venv",
    "v0-4-0-pin-before-floor",
    "v0-4-0-helper-import-before",
    "v0-4-0-helper-import-after",
    "v0-4-0-dependency-import-before",
    "v0-4-0-dependency-import-after",
    "v0-4-0-doc-targets-before",
    "v0-4-0-doc-targets-after",
    "v0-4-0-requires-python",
    "v0-4-0-python313-wheel-rejection",
)


def _load_project_metadata() -> tuple[str, str]:
    """Load the package version and interpreter requirement once for the suite."""
    project = tomllib.loads(
        (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]
    return str(project["version"]), str(project["requires-python"])


_PROJECT_VERSION, _PROJECT_REQUIRES_PYTHON = _load_project_metadata()


def _examples_by_id() -> dict[str, MarkdownExample]:
    """Load the published migration guide examples by stable identifier."""
    return {
        example.identifier: example for example in load_markdown_examples(_GUIDE_PATH)
    }


def _heading_index(
    headings: list[tuple[int, str]],
    level: int,
    title_prefix: str,
) -> int:
    """Find a heading by level and case-insensitive title prefix."""
    return next(
        index
        for index, (heading_level, title) in enumerate(headings)
        if heading_level == level and title.casefold().startswith(title_prefix)
    )


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
    assert imports, f"{example.identifier} must contain at least one import"
    return tuple(imports)


def _stub_program(directory: Path, name: str) -> Path:
    """Create an allowlisted command that records its arguments."""
    command = directory / name
    command.write_text(
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
    return command


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
    interpreter = Program(sys.executable)
    catalogue = ProgramCatalogue.from_programs(
        interpreter, name="migration-guide-examples"
    )
    log_path = tmp_path / "commands.jsonl"
    context = ExecutionContext(cwd=tmp_path, env={"DOC_EXAMPLE_LOG": str(log_path)})
    with scoped(catalogue=catalogue):
        for command in commands:
            program_name = command[0]
            if program_name == ".venv/bin/python":
                program_name = "python"
            stub = programs.get(program_name)
            assert stub is not None, (
                f"{example.identifier} uses an unapproved executable {command[0]!r}"
            )
            result = sh.make(interpreter, catalogue=catalogue)(
                str(stub), *command[1:]
            ).run_sync(context=context)
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
    missing_names: set[str] = set()
    for module_name, names in _imports_for_example(_examples_by_id()[example_id]):
        module = importlib.import_module(module_name)
        absent_names = tuple(name for name in names if not hasattr(module, name))
        assert absent_names == names, (
            f"the historical import must fail for {module_name}"
        )
        missing_names.update(absent_names)
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


def test_documentation_target_migration_examples_use_the_new_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The replacement example executes against the current documentation API."""
    examples = _examples_by_id()
    before = examples["v0-4-0-doc-targets-before"].source
    after = examples["v0-4-0-doc-targets-after"].source

    ast.parse(before)
    ast.parse(after)
    readme = tmp_path / "README.md"
    readme.write_text(
        '```toml\n[dependencies]\nmy-crate = "0.1.0"\n```\n',
        encoding="utf-8",
    )
    namespace = {
        "workspace_root": tmp_path,
        "documentation": config_module.DocumentationConfig(globs=("README.md",)),
        "target_version": "1.2.0",
        "updated_crates": {"my-crate"},
    }
    before_script = tmp_path / "migration_example_before.py"
    before_script.write_text(before, encoding="utf-8")
    before_namespace = namespace | {
        "resolve_documentation_targets": bump_docs.resolve_documentation_targets,
        "update_documentation_files": bump_docs.update_documentation_files,
        "dry_run": False,
    }
    with pytest.raises(TypeError):
        runpy.run_path(str(before_script), init_globals=before_namespace)

    example_script = tmp_path / "migration_example.py"
    example_script.write_text(after, encoding="utf-8")

    runpy.run_path(str(example_script), init_globals=namespace)

    assert 'my-crate = "1.2.0"' in readme.read_text(encoding="utf-8"), (
        "the documented after example must update the configured README"
    )
    assert capsys.readouterr().out == f"{readme}\n", (
        "the documented iteration must print the resolved README path"
    )


def test_requires_python_example_matches_project_metadata() -> None:
    """The documented new interpreter floor must match package metadata."""
    example = _examples_by_id()["v0-4-0-requires-python"]

    assert "# 0.3.1\nRequires-Python: >=3.13" in example.source, (
        "the historical metadata example must retain its Python 3.13 floor"
    )
    assert "# 0.4.0\nRequires-Python: >=3.14" in example.source, (
        "the frozen 0.4.0 release example must keep its Python 3.14 floor"
    )
    if str(_PROJECT_VERSION).startswith("0.4."):
        assert _PROJECT_REQUIRES_PYTHON == ">=3.14", (
            "the live 0.4.x project metadata must match its migration guide"
        )


def test_python313_rejection_example_states_the_package_constraint() -> None:
    """The installer transcript must agree with the current Python floor."""
    example = _examples_by_id()["v0-4-0-python313-wheel-rejection"]

    assert "$ python3.13 -m pip install lading-0.4.0-py3-none-any.whl" in (
        example.source
    ), "the installation example must use Python 3.13 and the 0.4.0 wheel"
    assert "3.13.x not in '>=3.14'" in example.source, (
        "the pip example must explain rejection against the >=3.14 requirement"
    )


def test_migration_checklist_follows_tasks_and_unaffected_behaviour() -> None:
    """The checklist follows every numbered task and unaffected behaviour."""
    guide = _GUIDE_PATH.read_text(encoding="utf-8")
    tokens = MarkdownIt("commonmark").parse(guide)
    headings = [
        (int(token.tag[1:]), tokens[index + 1].content)
        for index, token in enumerate(tokens)
        if token.type == "heading_open"
    ]
    last_task = _heading_index(headings, 2, "4.")
    unaffected = _heading_index(headings, 2, "what does not need to change")
    checklist = _heading_index(headings, 2, "migration checklist")
    optional_adoption = _heading_index(headings, 3, "optional adoption")

    assert last_task < unaffected < checklist < optional_adoption, (
        "required tasks and unaffected behaviour must precede the checklist"
    )


def test_uv_upgrade_updates_project_floor_before_adding_package() -> None:
    """The project metadata floor must be raised before resolving 0.4.0."""
    example = _examples_by_id()["v0-4-0-upgrade-uv"]
    lines = example.source.splitlines()
    floor_update = next(
        index
        for index, line in enumerate(lines)
        if line.lstrip().startswith("#")
        and line.lstrip()[1:].lstrip().startswith("requires-python")
    )
    assert (
        tomllib.loads(lines[floor_update].lstrip()[1:].strip())["requires-python"]
        == ">=3.14"
    ), "the documented project floor must be Python 3.14"
    package_add = next(
        index
        for index, line in enumerate(lines)
        if (arguments := shlex.split(line, comments=True))[:2] == ["uv", "add"]
        and "lading==0.4.0" in arguments
    )

    assert floor_update < package_add, (
        "the Python metadata floor must be updated before the package is added"
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
