"""Shared fixtures and helper fakes for publish check workflows."""

from __future__ import annotations

import contextlib
import dataclasses as dc
import importlib.util
import io
import sys
import typing as typ
from pathlib import Path

import pytest

if typ.TYPE_CHECKING:
    from types import ModuleType

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

RunCallable = typ.Callable[[list[str]], tuple[int, str, str]]


@dc.dataclass(frozen=True)
class CommandFailureTestCase:
    """Describe an expected crate failure and associated log fragments."""

    crate: str
    result_kwargs: dict[str, object]
    expected_exit_fragment: str | None
    expected_logs: tuple[str, ...]
    unexpected_logs: tuple[str, ...]


@dc.dataclass(frozen=True)
class CargoTestContext:
    """Test context container for cargo command scenarios."""

    patch_local_runner: typ.Callable[[RunCallable], FakeLocal]
    fake_workspace: Path
    caplog: pytest.LogCaptureFixture
    run_publish_check_module: ModuleType


@dc.dataclass
class WorkspaceMocks:
    """Bundle of mock functions for workspace operations."""

    record: typ.Callable[[str], typ.Callable[[Path], None]]
    fake_apply: typ.Callable[..., None]
    fake_remove: typ.Callable[[Path, str], None]


@dc.dataclass
class WorkflowTestConfig:
    """Configuration bundle for workflow integration scaffolding."""

    workspace_name: str
    crate_order: tuple[str, ...] = ("demo-crate",)


def _load_module_from_scripts(module_name: str, script_filename: str) -> ModuleType:
    """Load ``module_name`` from ``scripts`` while guarding against import issues."""
    script_path = SCRIPTS_DIR / script_filename
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    if spec is None or spec.loader is None:
        msg = f"Failed to load module spec for {module_name!r} from {script_path}"
        raise RuntimeError(msg)

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def run_publish_check_module() -> ModuleType:
    """Load ``run_publish_check`` as a real module for integration tests."""
    return _load_module_from_scripts("run_publish_check", "run_publish_check.py")


@pytest.fixture(scope="module")
def publish_workspace_module() -> ModuleType:
    """Load ``publish_workspace`` as a module for integration tests."""
    return _load_module_from_scripts("publish_workspace", "publish_workspace.py")


@pytest.fixture
def fake_workspace(tmp_path: Path) -> Path:
    """Provision a fake workspace tree used by cargo command tests."""
    workspace = tmp_path / "workspace"
    (workspace / "crates" / "demo").mkdir(parents=True)
    return workspace


@pytest.fixture
def mock_cargo_runner(
    monkeypatch: pytest.MonkeyPatch, run_publish_check_module: ModuleType
) -> list[tuple[object, tuple[str, ...], typ.Callable[[str, object], bool] | None]]:
    """Capture invocations made to ``run_cargo_command``."""
    calls: list[
        tuple[object, tuple[str, ...], typ.Callable[[str, object], bool] | None]
    ] = []

    def fake_run_cargo(
        context: run_publish_check_module.CargoCommandContext,
        command: typ.Sequence[str],
        *,
        on_failure: typ.Callable[[str, run_publish_check_module.CommandResult], bool]
        | None = None,
    ) -> None:
        calls.append((context, tuple(command), on_failure))

    monkeypatch.setattr(run_publish_check_module, "run_cargo_command", fake_run_cargo)
    return calls


@pytest.fixture
def patch_local_runner(
    monkeypatch: pytest.MonkeyPatch, run_publish_check_module: ModuleType
) -> typ.Callable[[RunCallable], FakeLocal]:
    """Install a ``FakeLocal`` around the provided callable."""

    def _install(run_callable: RunCallable) -> FakeLocal:
        fake_local = FakeLocal(run_callable)
        monkeypatch.setattr(run_publish_check_module, "local", fake_local)
        return fake_local

    return _install


@pytest.fixture
def cargo_test_context(
    patch_local_runner: typ.Callable[[RunCallable], FakeLocal],
    fake_workspace: Path,
    caplog: pytest.LogCaptureFixture,
    run_publish_check_module: ModuleType,
) -> CargoTestContext:
    """Bundle fixtures required for cargo command assertions."""
    return CargoTestContext(
        patch_local_runner=patch_local_runner,
        fake_workspace=fake_workspace,
        caplog=caplog,
        run_publish_check_module=run_publish_check_module,
    )


class FakeCargoInvocation:
    """Record a cargo invocation and proxy execution to the fake runner."""

    def __init__(self, local: FakeLocal, args: list[str]) -> None:
        """Store the invocation context for later assertions."""
        self._local = local
        self._args = ["cargo", *args]

    def run(
        self, *, retcode: object | None, timeout: int | None
    ) -> tuple[int, str, str]:
        """Record an invocation and delegate to the configured callable."""
        self._local.invocations.append(self._args)
        return self._local.run_callable(self._args)

    def popen(self, *_args: object, **_kwargs: object) -> FakeProcess:
        """Return a ``FakeProcess`` that mimics streaming behaviour."""
        self._local.invocations.append(self._args)
        return FakeProcess(self._args, self._local.run_callable)


class FakeCargo:
    """Proxy indexing calls into ``FakeCargoInvocation`` instances."""

    def __init__(self, local: FakeLocal) -> None:
        """Initialise the cargo proxy for a fake local environment."""
        self._local = local

    def __getitem__(self, args: object) -> FakeCargoInvocation:
        """Return an invocation wrapper for the provided command arguments."""
        extras = list(args) if isinstance(args, list | tuple) else [str(args)]
        return FakeCargoInvocation(self._local, extras)


class FakeProcess:
    """Simulate a subprocess for cargo command tests."""

    def __init__(self, args: list[str], run_callable: RunCallable) -> None:
        """Populate stdout/stderr buffers using the provided callable."""
        self.args = args
        return_code, stdout, stderr = run_callable(args)
        self._return_code = return_code
        self.stdout = io.BufferedReader(_to_buffer(stdout))
        self.stderr = io.BufferedReader(_to_buffer(stderr))

    def wait(self, timeout: int | None = None) -> int:
        """Return the pre-configured exit code immediately."""
        return self._return_code

    def kill(self) -> None:
        """Simulate killing the underlying process."""
        self._return_code = -9


def _to_buffer(stream: str | bytes) -> io.BytesIO:
    """Convert string or bytes into a BytesIO buffer."""
    data = stream if isinstance(stream, bytes) else stream.encode("utf-8")
    return io.BytesIO(data)


class FakeLocal:
    """Mimic a fabric ``local`` helper for cargo orchestration tests."""

    def __init__(self, run_callable: RunCallable) -> None:
        """Store the callable that will service fake local invocations."""
        self.run_callable = run_callable
        self.cwd_calls: list[Path] = []
        self.env_calls: list[dict[str, str]] = []
        self.invocations: list[list[str]] = []

    def __getitem__(self, command: str) -> FakeCargo:
        """Return a ``FakeCargo`` proxy for the ``cargo`` command."""
        if command != "cargo":
            msg = (
                f"FakeLocal only understands the 'cargo' command, received {command!r}"
            )
            raise RuntimeError(msg)
        return FakeCargo(self)

    def cwd(self, path: Path) -> contextlib.AbstractContextManager[None]:
        """Record the working directory change for later assertions."""
        self.cwd_calls.append(path)
        return contextlib.nullcontext()

    def env(self, **kwargs: str) -> contextlib.AbstractContextManager[None]:
        """Record environment mutations for later assertions."""
        self.env_calls.append(kwargs)
        return contextlib.nullcontext()


def _setup_basic_workflow_mocks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    run_publish_check_module: ModuleType,
    *,
    config: WorkflowTestConfig,
) -> Path:
    """Prepare shared workspace and helper mocks for workflow integration tests."""
    workspace_dir = tmp_path / config.workspace_name
    workspace_dir.mkdir()
    monkeypatch.setattr(
        run_publish_check_module.tempfile, "mkdtemp", lambda: str(workspace_dir)
    )
    monkeypatch.setattr(
        run_publish_check_module, "export_workspace", lambda _dest: None
    )
    monkeypatch.setattr(
        run_publish_check_module, "prune_workspace_members", lambda _manifest: None
    )
    monkeypatch.setattr(
        run_publish_check_module, "strip_patch_section", lambda _manifest: None
    )
    monkeypatch.setattr(
        run_publish_check_module, "workspace_version", lambda _manifest: "1.0.0"
    )
    monkeypatch.setattr(
        run_publish_check_module,
        "apply_workspace_replacements",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        run_publish_check_module, "package_crate", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        run_publish_check_module, "check_crate", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(run_publish_check_module, "CRATE_ORDER", config.crate_order)
    return workspace_dir
