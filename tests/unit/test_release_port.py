"""Tests that the uploader's dependency direction is the one it claims.

The port exists so the policy module can be imported with no cuprum and no
process edge present. That is a structural property, so it is asserted
structurally -- by parsing each module's imports rather than by reading them --
because the failure mode is precisely that a later edit adds an import that
looks harmless and quietly restores the coupling. A comment claiming the
direction would not survive that; these tests would fail.
"""

import ast
from pathlib import Path

import pytest

SCRIPT_DIRECTORY = Path(__file__).resolve().parents[2] / "scripts"
POLICY_PATH = SCRIPT_DIRECTORY / "release_wheel_upload.py"
ADAPTER_PATH = SCRIPT_DIRECTORY / "release_gh.py"
PORT_PATH = SCRIPT_DIRECTORY / "release_port.py"
ENTRYPOINT_PATH = SCRIPT_DIRECTORY / "upload_release_wheels.py"


def _imported_names(path: Path) -> set[str]:
    """Return every module name ``path`` imports.

    Returns
    -------
    set[str]
        The dotted module names at the root of each import statement.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        match node:
            case ast.Import():
                names.update(alias.name.split(".")[0] for alias in node.names)
            case ast.ImportFrom() if node.module is not None:
                names.add(node.module.split(".")[0])
    return names


def test_the_policy_does_not_import_cuprum() -> None:
    """The policy module must stay free of the process technology.

    This is the property the port was introduced for. ``release_gh`` imports
    cuprum; if the policy imported either cuprum or the adapter, then every
    test of what to upload would pay for importing the process edge.
    """
    imported = _imported_names(POLICY_PATH)

    assert "cuprum" not in imported, (
        "release_wheel_upload imports cuprum; the port exists so it does not"
    )
    assert "release_gh" not in imported, (
        "release_wheel_upload imports the cuprum adapter; the policy's "
        "dependency must point at the port instead"
    )


def test_the_port_imports_nothing_but_the_standard_library() -> None:
    """The port is the module both sides depend on, so it depends on neither.

    A port that imported its adapter, or the technology the adapter wraps,
    would be the same inversion one module further along.
    """
    imported = _imported_names(PORT_PATH)

    assert "cuprum" not in imported, "release_port must not import cuprum"
    assert "release_gh" not in imported, "release_port must not import the adapter"
    assert "release_wheel_upload" not in imported, (
        "release_port must not import the policy that depends on it"
    )
    assert not {name for name in imported if name not in _STDLIB}, (
        f"release_port imports beyond the standard library: {sorted(imported)}"
    )


#: The standard-library modules ``release_port`` is permitted to import. Listing
#: them makes an accidental third-party dependency a test failure rather than
#: something to notice later.
_STDLIB = frozenset({"__future__", "collections", "dataclasses", "typing"})


def test_the_adapter_imports_the_port_and_not_the_policy() -> None:
    """The adapter translates into the port's type; it does not know the policy.

    The direction is adapter -> port, and the entrypoint -> both. An adapter
    importing the policy would mean the process edge could reach the upload
    logic, which is the arrow reversed.
    """
    imported = _imported_names(ADAPTER_PATH)

    assert "release_port" in imported, (
        "release_gh must import CommandOutcome from the port, not define it"
    )
    assert "release_wheel_upload" not in imported, (
        "release_gh must not import the policy module"
    )


def _function(path: Path, name: str) -> ast.FunctionDef:
    """Return the named function definition from ``path``.

    Returns
    -------
    ast.FunctionDef
        The definition.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == name
        ),
        None,
    )
    message = f"{path.name} declares no function named {name}"
    assert found is not None, message
    return found


def test_the_policy_defaults_no_process_dependency() -> None:
    """Production wiring is bound at the composition root, not in the policy.

    A default runner in ``upload_wheels`` would name the adapter in this
    module's namespace, which is the coupling the port removes -- and it would
    do so in the one place a structural import check cannot see, since a
    default is not an import. The same holds one level up: an ``attach_wheels``
    that defaulted a whole ``Dependencies`` would put the runner back, since
    only the composition root can build one.
    """
    upload = _function(POLICY_PATH, "upload_wheels")
    defaults = dict(zip(upload.args.kwonlyargs, upload.args.kw_defaults, strict=True))
    runner = next(arg for arg in upload.args.kwonlyargs if arg.arg == "run")

    assert runner.annotation is not None, "the runner parameter must be annotated"
    assert defaults[runner] is None, (
        "upload_wheels must not default its runner; bind it at the composition root"
    )

    attach = _function(POLICY_PATH, "attach_wheels")
    optional = dict(
        zip(
            [arg.arg for arg in attach.args.args],
            [None] * (len(attach.args.args) - len(attach.args.defaults))
            + list(attach.args.defaults),
            strict=True,
        )
    )

    assert optional["dependencies"] is None, (
        "attach_wheels must require its dependencies; a default would have to "
        "name a runner"
    )


def _partial_bindings(path: Path) -> dict[str, ast.expr]:
    """Return what each ``partial(...)`` call in ``path`` binds by keyword.

    Returns
    -------
    dict[str, ast.expr]
        Keyword name to the expression bound to it, across every ``partial``
        call in the module. A name bound twice keeps the last, which is enough
        for a module that binds one production wiring.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        keyword.arg: keyword.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "partial"
        for keyword in node.keywords
        if keyword.arg is not None
    }


def test_the_composition_root_binds_the_production_runner() -> None:
    """Something must still bind them, or production would upload nothing.

    The check above is only safe because this one holds: the removal of the
    default is a move, not a deletion. The binding is read out of the call
    rather than searched for as text, because a substring match for
    ``upload_wheels`` would still pass if the runner were dropped.
    """
    imported = _imported_names(ENTRYPOINT_PATH)
    bound = _partial_bindings(ENTRYPOINT_PATH).get("run")

    assert "release_gh" in imported, (
        "the entrypoint must import the production adapter to bind it"
    )
    assert bound is not None, (
        "the composition root must bind the runner into upload_wheels"
    )
    assert isinstance(bound, ast.Name), (
        f"the runner must be named directly, not built: {ast.dump(bound)}"
    )
    assert bound.id == "run_gh", (
        f"the composition root binds {bound.id}, not the production run_gh"
    )


@pytest.mark.parametrize(
    "path", [POLICY_PATH, ADAPTER_PATH, PORT_PATH, ENTRYPOINT_PATH]
)
def test_every_module_stays_under_the_line_limit(path: Path) -> None:
    """AGENTS.md caps a code file at 400 lines; the split is what keeps it true.

    Guarded here because this refactor moved code between three modules and
    the limit is not otherwise enforced by a gate.
    """
    lines = len(path.read_text(encoding="utf-8").splitlines())

    assert lines <= 400, f"{path.name} is {lines} lines, over the 400-line cap"
