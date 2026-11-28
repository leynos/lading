"""End-to-end cover for ``lading publish --expect-digests`` as a user runs it.

The in-process scenarios (``tests/bdd/features/publish_expect_digests.feature``)
drive ``publish.run`` with doubles. These cross the boundary a user crosses:
argument parsing from a real argv, the configuration and environment
resolution in :func:`lading.cli.main`, a real staged copy of a real workspace,
and exit status and streams of a separate process. Cargo is a stub on ``PATH``
(``tests/e2e/helpers/stub_cargo.py``) that records every invocation, so the
tests can say which Cargo commands ran.

The registry download is the one thing a subprocess cannot be handed a fake
for, so the process is started through a one-line launcher that points the
downloader at a loopback server the test owns; nothing reaches crates.io.

Cases covered here: a malformed digest file, a missing entry, an extra entry, a
non-crates.io ``CARGO_REGISTRY_DEFAULT`` (each must stage nothing and run no
Cargo command beyond workspace discovery), a mismatch that stops a dry run
before any publish, and an identical published archive that is skipped in a
live run. The in-process scenarios keep the remaining registry and upload cases.
"""

import hashlib
import http.server
import json
import os
import subprocess
import sys
import threading
import typing as typ
from pathlib import Path

import pytest

from lading.commands.publish_staging import STAGING_PREFIX

if typ.TYPE_CHECKING:
    import collections.abc as cabc

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_CRATES = ("alpha", "beta")

_STUB_SCRIPT = Path(__file__).parent / "helpers" / "stub_cargo.py"
# `cargo` on PATH: a two-line script that runs the stub (see its docstring).
_CARGO_ON_PATH = (
    "#!{python}\nimport runpy; runpy.run_path({script!r}, run_name='__main__')\n"
)
_LAUNCHER = (
    "import os, sys; import lading.commands.publish_registry as r; "
    "r._ARCHIVE_HOST = os.environ['LADING_E2E_ARCHIVE_HOST']; "
    "from lading.cli import main; sys.exit(main())"
)


def _digest(payload: str) -> str:
    """Return the SHA-256 of an archive the stub writes for ``payload``."""
    return hashlib.sha256(payload.encode()).hexdigest()


def _digest_line(crate: str, payload: str | None = None) -> str:
    """Return the ``sha256sum`` line for ``crate``'s stub archive."""
    return f"{_digest(payload or f'archive:{crate}')}  {crate}-0.1.0.crate\n"


class _Rig:
    """A workspace on disk, a stub Cargo, and the means to run lading."""

    def __init__(self, tmp_path: Path, archive_host: str) -> None:
        """Lay out the workspace, the stub and the scratch directories."""
        self.root = tmp_path / "workspace"
        self.staging = tmp_path / "staging"
        self.log = tmp_path / "cargo.log"
        self.digests = self.root / "digests.txt"
        self._bin = tmp_path / "bin"
        self._cargo_home = tmp_path / "cargo-home"
        self._archive_host = archive_host
        for directory in (self.staging, self._bin, self._cargo_home):
            directory.mkdir()
        self._write_workspace()
        stub = self._bin / "cargo"
        stub.write_text(
            _CARGO_ON_PATH.format(python=sys.executable, script=str(_STUB_SCRIPT)),
            encoding="utf-8",
        )
        stub.chmod(0o755)

    def _write_workspace(self) -> None:
        """Write a two-crate workspace in which ``beta`` depends on ``alpha``."""
        members = ", ".join(f'"{name}"' for name in _CRATES)
        (self.root / "Cargo.toml").parent.mkdir(parents=True)
        (self.root / "Cargo.toml").write_text(
            f"[workspace]\nmembers = [{members}]\n", encoding="utf-8"
        )
        for name in _CRATES:
            (self.root / name).mkdir()
            (self.root / name / "Cargo.toml").write_text(
                f'[package]\nname = "{name}"\nversion = "0.1.0"\nedition = "2021"\n',
                encoding="utf-8",
            )

    def run(
        self, *arguments: str, env: cabc.Mapping[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run ``lading publish`` as a separate process with Cargo stubbed."""
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("CARGO_")
        }
        environment |= {
            "PATH": f"{self._bin}{os.pathsep}{os.environ['PATH']}",
            "TMPDIR": str(self.staging),
            "CARGO_HOME": str(self._cargo_home),
            "STUB_CARGO_LOG": str(self.log),
            "LADING_E2E_ARCHIVE_HOST": self._archive_host,
            **(env or {}),
        }
        argv = [
            sys.executable,
            "-c",
            _LAUNCHER,
            "--workspace-root",
            str(self.root),
            "publish",
            "--skip-preflight",
            *arguments,
        ]
        return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv, no shell
            argv,
            capture_output=True,
            text=True,
            check=False,
            cwd=REPOSITORY_ROOT,
            env=environment,
            timeout=180,
        )

    def cargo_subcommands(self) -> list[str]:
        """Return the Cargo subcommands run, in order, crate directories included."""
        if not self.log.exists():
            return []
        entries = [json.loads(line) for line in self.log.read_text().splitlines()]
        return [
            f"{entry['argv'][0]}:{Path(entry['cwd']).name}"
            if entry["argv"][0] != "metadata"
            else "metadata"
            for entry in entries
        ]

    def cargo_commands(self, subcommand: str) -> list[list[str]]:
        """Return the full argv of every ``cargo <subcommand>`` run."""
        entries = [json.loads(line) for line in self.log.read_text().splitlines()]
        return [e["argv"] for e in entries if e["argv"][0] == subcommand]

    def staged_trees(self) -> list[Path]:
        """Return any staging directories left under the scratch ``TMPDIR``."""
        return sorted(self.staging.glob(f"{STAGING_PREFIX}*"))


class _ArchiveServer:
    """A loopback server standing in for the crates.io archive host."""

    def __init__(self, published: dict[str, bytes]) -> None:
        """Serve ``published`` (crate name to archive bytes); 404 the rest."""
        served = published

        class Handler(http.server.BaseHTTPRequestHandler):
            """Answer ``/<name>/<name>-<version>.crate``."""

            def do_GET(self) -> None:
                """Serve a published archive or a 404."""
                name = self.path.strip("/").split("/")[0]
                body = served.get(name)
                self.send_response(404 if body is None else 200)
                self.send_header("Content-Length", str(len(body or b"")))
                self.end_headers()
                self.wfile.write(body or b"")

            def log_message(self, format: str, *args: object) -> None:  # ruff: ignore[builtin-argument-shadowing] -- the http.server hook signature
                """Stay quiet."""

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> _ArchiveServer:
        """Start serving."""
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        """Stop serving and release the port."""
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()


@pytest.fixture
def rig(tmp_path: Path) -> cabc.Iterator[_Rig]:
    """Provide a rig whose registry has published nothing."""
    with _ArchiveServer({}) as server:
        yield _Rig(tmp_path, server.url)


def _assert_nothing_ran(rig: _Rig, result: subprocess.CompletedProcess[str]) -> None:
    """Assert lading failed before staging, packaging or uploading anything."""
    assert result.returncode == 1, f"lading must fail: {result.stdout}{result.stderr}"
    assert set(rig.cargo_subcommands()) <= {"metadata"}, (
        f"only workspace discovery may run Cargo: {rig.cargo_subcommands()}"
    )
    assert "Staged workspace created" not in result.stderr, "nothing may be staged"
    assert not rig.staged_trees(), "no staging directory may exist"


@pytest.mark.parametrize(
    ("digests", "env", "expected"),
    [
        ("not a digest\n", {}, "Malformed digest entries"),
        (_digest_line("alpha"), {}, "no entry for beta-0.1.0.crate"),
        (
            _digest_line("alpha") + _digest_line("beta") + _digest_line("delta"),
            {},
            "delta-0.1.0.crate, which is not in the plan",
        ),
        (
            _digest_line("alpha") + _digest_line("beta"),
            {"CARGO_REGISTRY_DEFAULT": "internal"},
            "crates.io only",
        ),
    ],
    ids=["malformed", "missing_entry", "extra_entry", "other_registry"],
)
def test_invalid_expectations_fail_before_anything_runs(
    rig: _Rig, digests: str, env: dict[str, str], expected: str
) -> None:
    """Each refusal that needs no network stops the real process early."""
    rig.digests.write_text(digests, encoding="utf-8")

    result = rig.run("--live", "--expect-digests", str(rig.digests), env=env)

    _assert_nothing_ran(rig, result)
    assert expected in result.stderr, f"the diagnostic must say why: {result.stderr}"


def test_a_mismatch_stops_a_dry_run_before_any_publish(rig: _Rig) -> None:
    """A packaged archive that differs is refused and nothing is published."""
    rig.digests.write_text(
        _digest_line("alpha") + _digest_line("beta"), encoding="utf-8"
    )

    result = rig.run(
        "--expect-digests", str(rig.digests), env={"STUB_ARCHIVE_BETA": "tampered"}
    )

    assert result.returncode == 1, f"the mismatch must fail the run: {result.stderr}"
    assert "differs from the digest expected" in result.stderr, result.stderr
    assert _digest("tampered") in result.stderr, "the actual digest must be named"
    assert rig.cargo_subcommands().count("publish:alpha") == 0, "no publish may run"
    assert not rig.cargo_commands("publish"), "cargo publish must never have run"
    packaged = [name for name in rig.cargo_subcommands() if name.startswith("package")]
    assert packaged == ["package:alpha", "package:beta"], packaged
    assert all("--locked" in argv for argv in rig.cargo_commands("package")), (
        "cargo package must run --locked"
    )


def test_an_identical_published_archive_is_skipped_in_a_live_run(
    tmp_path: Path,
) -> None:
    """Resuming a release: a published, identical crate is not packaged again."""
    with _ArchiveServer({"alpha": b"archive:alpha"}) as server:
        rig = _Rig(tmp_path, server.url)
        rig.digests.write_text(
            _digest_line("alpha") + _digest_line("beta"), encoding="utf-8"
        )

        result = rig.run("--live", "--expect-digests", str(rig.digests))

    assert result.returncode == 0, f"the run must succeed: {result.stderr}"
    assert "alpha 0.1.0: already published and identical" in result.stdout, (
        result.stdout
    )
    ran = [name for name in rig.cargo_subcommands() if name != "metadata"]
    assert ran == ["package:beta", "publish:beta"], (
        f"only beta may be packaged and published: {ran}"
    )
    assert not rig.staged_trees(), "the staged copy must be removed afterwards"
