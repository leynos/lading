"""Tests the uploader's domain port and adapter dependency direction."""

import ast
from pathlib import Path

import pytest

SCRIPT_DIRECTORY = Path(__file__).resolve().parents[2] / "scripts"
POLICY_PATH = SCRIPT_DIRECTORY / "release_wheel_upload.py"
ADAPTER_PATH = SCRIPT_DIRECTORY / "release_gh.py"
PORT_PATH = SCRIPT_DIRECTORY / "release_port.py"
ENTRYPOINT_PATH = SCRIPT_DIRECTORY / "upload_release_wheels.py"
_STDLIB = frozenset({"collections", "pathlib", "typing"})


def _imported_names(path: Path) -> set[str]:
    """Return root module names imported by ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        match node:
            case ast.Import():
                names.update(alias.name.split(".")[0] for alias in node.names)
            case ast.ImportFrom() if node.module is not None:
                names.add(node.module.split(".")[0])
    return names


def _function(path: Path, name: str) -> ast.FunctionDef:
    """Return one named function from the module AST."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == name
        ),
        None,
    )
    assert found is not None, f"{path.name} declares no function named {name}"
    return found


def test_policy_does_not_import_the_process_adapter() -> None:
    """Policy stays importable without cuprum or its GitHub adapter."""
    imported = _imported_names(POLICY_PATH)
    assert "cuprum" not in imported, "policy must not import cuprum"
    assert "release_gh" not in imported, "policy must not import its adapter"


def test_port_imports_only_the_standard_library() -> None:
    """Both sides can depend on the port without pulling in infrastructure."""
    imported = _imported_names(PORT_PATH)
    unexpected = sorted(name for name in imported if name not in _STDLIB)
    assert not unexpected, f"domain port must import only stdlib modules: {unexpected}"


def test_adapter_imports_the_port_and_not_the_policy() -> None:
    """The adapter points inward and does not know policy decisions."""
    imported = _imported_names(ADAPTER_PATH)
    assert "release_port" in imported, "adapter must implement the domain port"
    assert "release_wheel_upload" not in imported, "adapter must not import policy"


def test_policy_requires_the_release_asset_port() -> None:
    """Tests and production wiring must supply the domain boundary explicitly."""
    upload = _function(POLICY_PATH, "upload_distributions")
    defaults = dict(zip(upload.args.kwonlyargs, upload.args.kw_defaults, strict=True))
    assets = next(
        argument for argument in upload.args.kwonlyargs if argument.arg == "assets"
    )
    assert assets.annotation is not None, "the port parameter must be annotated"
    assert defaults[assets] is None, "policy must require the release asset port"

    attach = _function(POLICY_PATH, "attach_distributions")
    optional = dict(
        zip(
            [argument.arg for argument in attach.args.args],
            [None] * (len(attach.args.args) - len(attach.args.defaults))
            + list(attach.args.defaults),
            strict=True,
        )
    )
    assert optional["dependencies"] is None, "attach must require injected dependencies"


def test_composition_root_constructs_the_github_adapter() -> None:
    """Production composition connects the policy to its GitHub adapter."""
    assert "release_gh" in _imported_names(ENTRYPOINT_PATH), (
        "production entrypoint must import the GitHub adapter"
    )
    tree = ast.parse(ENTRYPOINT_PATH.read_text(encoding="utf-8"))
    assert any(
        isinstance(node, ast.Name) and node.id == "GitHubReleaseAssets"
        for node in ast.walk(tree)
    ), "production entrypoint must instantiate the GitHub asset adapter"


def test_policy_does_not_construct_or_parse_gh_commands() -> None:
    """Command arguments and process output belong to the adapter."""
    tree = ast.parse(POLICY_PATH.read_text(encoding="utf-8"))
    calls = [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]
    assert "splitlines" not in calls, "policy must not parse process output"
    assert '"--json"' not in POLICY_PATH.read_text(encoding="utf-8"), (
        "policy must not construct GitHub CLI arguments"
    )


@pytest.mark.parametrize(
    "path", [POLICY_PATH, ADAPTER_PATH, PORT_PATH, ENTRYPOINT_PATH]
)
def test_every_module_stays_under_the_line_limit(path: Path) -> None:
    """The split modules stay within the repository's 400-line cap."""
    lines = len(path.read_text(encoding="utf-8").splitlines())
    assert lines <= 400, f"{path.name} is {lines} lines, over the 400-line cap"
