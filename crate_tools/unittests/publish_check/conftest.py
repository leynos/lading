"""Shared fixtures and helper fakes for publish check workflows."""

from __future__ import annotations

import collections.abc as cabc
import contextlib
import dataclasses as dc
import importlib.util
import io
import sys
import typing as typ
from pathlib import Path
from types import SimpleNamespace

import pytest

if typ.TYPE_CHECKING:
    from types import ModuleType

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

StreamData = str | bytes | cabc.Sequence[str | bytes]
RunCallable = typ.Callable[[list[str]], tuple[int, StreamData, StreamData]]


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
    ) -> tuple[int, StreamData, StreamData]:
        """Record an invocation and delegate to the configured callable."""
        self._local.invocations.append(self._args)
        return self._local.run_callable(self._args)

    def popen(self, *_args: object, **kwargs: object) -> SimpleNamespace:
        """Return a ``SimpleNamespace`` process wired to the fake runner."""
        self._local.invocations.append(self._args)
        self._local.popen_kwargs.append(dict(kwargs))
        return _build_process(self._args, self._local.run_callable)


class FakeCargo:
    """Proxy indexing calls into ``FakeCargoInvocation`` instances."""

    def __init__(self, local: FakeLocal) -> None:
        """Initialise the cargo proxy for a fake local environment."""
        self._local = local

    def __getitem__(self, args: object) -> FakeCargoInvocation:
        """Return an invocation wrapper for the provided command arguments."""
        extras = list(args) if isinstance(args, list | tuple) else [str(args)]
        return FakeCargoInvocation(self._local, extras)


def _build_process(args: list[str], run_callable: RunCallable) -> SimpleNamespace:
    """Return a lightweight process namespace for cargo tests."""
    return_code, stdout_data, stderr_data = run_callable(args)
    state = {"return_code": return_code, "killed": False}

    def wait(timeout: int | None = None) -> int:
        del timeout
        return state["return_code"]

    def kill() -> None:
        state["return_code"] = -9
        state["killed"] = True

    return SimpleNamespace(
        args=args,
        stdout=_build_stream(stdout_data),
        stderr=_build_stream(stderr_data),
        wait=wait,
        kill=kill,
        state=state,
    )


def _build_stream(data: StreamData) -> io.IOBase:
    """Create a byte-stream object from the provided data."""
    if isinstance(data, cabc.Sequence) and not isinstance(
        data, (bytes, bytearray, str)
    ):
        chunks = [_ensure_bytes(chunk) for chunk in data]
        return _ChunkedStream(chunks)
    return io.BytesIO(_ensure_bytes(data))


def _ensure_bytes(value: str | bytes) -> bytes:
    """Encode text to bytes when necessary."""
    return value if isinstance(value, bytes) else value.encode("utf-8")


class _ChunkedStream(io.RawIOBase):
    """Raw stream that yields predefined byte chunks."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)

    def read(self, _size: int = -1) -> bytes:
        return self.read1(_size)

    def read1(self, _size: int = -1) -> bytes:
        if not self._chunks:
            return b""
        return self._chunks.pop(0)

    def readable(self) -> bool:
        return True

    def close(self) -> None:
        super().close()


class StreamRecorder:
    """Recording sink used to assert streaming behaviour."""

    def __init__(self) -> None:
        """Initialise in-memory buffers for captured writes."""
        self.writes: list[str] = []
        self.flushes: int = 0

    def write(self, text: str) -> int:
        """Capture a write and report the consumed length."""
        self.writes.append(text)
        return len(text)

    def flush(self) -> None:
        """Record a flush event for downstream assertions."""
        self.flushes += 1


class FakeLocal:
    """Mimic a fabric ``local`` helper for cargo orchestration tests."""

    def __init__(self, run_callable: RunCallable) -> None:
        """Store the callable that will service fake local invocations."""
        self.run_callable = run_callable
        self.cwd_calls: list[Path] = []
        self.env_calls: list[dict[str, str]] = []
        self.invocations: list[list[str]] = []
        self.popen_kwargs: list[dict[str, object]] = []

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
