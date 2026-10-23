"""Contract tests for the checksum-pinned `make install-nose` installer.

The target reuses a binary only when its version and executable digest match
the committed pins. Otherwise it downloads the official archive, checks its
digest, checks the executable extracted from it, and installs that exact file.
These tests replace the network and archive tools with deterministic stubs.
"""

import dataclasses as dc
import hashlib
import os
import shutil
import subprocess
import typing as typ
from pathlib import Path

import pytest

SCRIPT_DIRECTORY = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = SCRIPT_DIRECTORY.parent
MAKEFILE = REPOSITORY_ROOT / "Makefile"
NOSE_VERSION = "0.20.0"
TARGET = "x86_64-unknown-linux-gnu"
VARIABLE_SUFFIX = "X86_64_UNKNOWN_LINUX_GNU"
ARCHIVE_BYTES = b"deterministic test archive"

pytestmark = pytest.mark.skipif(
    shutil.which("make") is None,
    reason="`make` is required to exercise the installer target.",
)


class InstallerInvocation(typ.NamedTuple):
    """The recorded effects of one `make install-nose` run."""

    result: subprocess.CompletedProcess[str]
    """The completed Make process."""

    curl_arguments: list[str]
    """The arguments the stub `curl` received, if a download was attempted."""


@dc.dataclass(frozen=True, slots=True, kw_only=True)
class InstallerScenario:
    """Inputs that vary across installer contract tests."""

    installed_version: str | None = None
    tamper_cached_binary: bool = False
    archive_checksum: str | None = None
    binary_checksum: str | None = None
    system: str = "Linux"
    architecture: str = "x86_64"


def _nose_bytes(version: str) -> bytes:
    """Return a small executable that reports one nose version."""
    return f"#!/bin/sh\nprintf 'nose %s\\n' '{version}'\n".encode()


def _write_executable(path: Path, body: str | bytes) -> Path:
    """Write an executable stub script or binary fixture."""
    if isinstance(body, bytes):
        path.write_bytes(body)
    else:
        path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _stub_curl(directory: Path) -> Path:
    """Write a downloader stub that records its URL and copies a fixture."""
    return _write_executable(
        directory / "curl",
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$FAKE_CURL_LOG"\n'
        'while [ "$#" -gt 0 ]; do\n'
        '  case "$1" in\n'
        '    -o) output="$2"; shift 2 ;;\n'
        "    *) shift ;;\n"
        "  esac\n"
        "done\n"
        'cp "$FAKE_ARCHIVE" "$output"\n',
    )


def _stub_tar(directory: Path) -> Path:
    """Write an extractor stub that emits the pinned executable fixture."""
    return _write_executable(
        directory / "tar",
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$FAKE_TAR_LOG"\ncat "$FAKE_NOSE_BINARY"\n',
    )


def _stub_uname(directory: Path) -> Path:
    """Write a platform probe stub controlled by the test environment."""
    return _write_executable(
        directory / "uname",
        "#!/bin/sh\n"
        'case "$1" in\n'
        '  -s) printf "%s\\n" "$FAKE_UNAME_SYSTEM" ;;\n'
        '  -m) printf "%s\\n" "$FAKE_UNAME_ARCH" ;;\n'
        "  *) exit 2 ;;\n"
        "esac\n",
    )


def _stub_sha256sum(directory: Path) -> Path:
    """Write a portable hash command that reads the piped or redirected bytes."""
    return _write_executable(
        directory / "sha256sum",
        "#!/usr/bin/env python3\n"
        "import hashlib\n"
        "import sys\n"
        "print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest(), '-', sep='  ')\n",
    )


def _run_install_nose(
    tmp_path: Path,
    scenario: InstallerScenario | None = None,
) -> InstallerInvocation:
    """Run the real installer with deterministic network and archive stubs."""
    if scenario is None:
        scenario = InstallerScenario()
    make = shutil.which("make")
    assert make is not None, (
        "The module-level skipif should have excluded this test without make."
    )

    tools = tmp_path / "tools"
    tools.mkdir()
    nose_bin = tools / "nose"
    if scenario.installed_version is not None:
        _write_executable(nose_bin, _nose_bytes(scenario.installed_version))
        if scenario.tamper_cached_binary:
            nose_bin.write_bytes(nose_bin.read_bytes() + b"tampered")

    archive_path = tmp_path / "archive.fixture"
    archive_path.write_bytes(ARCHIVE_BYTES)
    nose_payload = tmp_path / "nose.fixture"
    nose_payload.write_bytes(_nose_bytes(NOSE_VERSION))
    curl_log = tmp_path / "curl-arguments"
    tar_log = tmp_path / "tar-arguments"

    _stub_curl(tmp_path)
    _stub_tar(tmp_path)
    _stub_uname(tmp_path)
    _stub_sha256sum(tmp_path)

    expected_archive = hashlib.sha256(ARCHIVE_BYTES).hexdigest()
    expected_binary = hashlib.sha256(_nose_bytes(NOSE_VERSION)).hexdigest()
    archive_checksum = scenario.archive_checksum or expected_archive
    binary_checksum = scenario.binary_checksum or expected_binary
    command = [
        make,
        "--no-print-directory",
        "-f",
        str(MAKEFILE),
        "install-nose",
        f"NOSE_BIN={nose_bin}",
        f"NOSE_ARCHIVE_SHA256_{VARIABLE_SUFFIX}={archive_checksum}",
        f"NOSE_BINARY_SHA256_{VARIABLE_SUFFIX}={binary_checksum}",
    ]
    environment = {
        **os.environ,
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "FAKE_ARCHIVE": str(archive_path),
        "FAKE_CURL_LOG": str(curl_log),
        "FAKE_NOSE_BINARY": str(nose_payload),
        "FAKE_TAR_LOG": str(tar_log),
        "FAKE_UNAME_SYSTEM": scenario.system,
        "FAKE_UNAME_ARCH": scenario.architecture,
    }
    result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed target and stubbed tools.
        command,
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    curl_arguments = (
        curl_log.read_text(encoding="utf-8").splitlines() if curl_log.exists() else []
    )
    return InstallerInvocation(result=result, curl_arguments=curl_arguments)


class TestMakeInstallNose:
    """The cached-versus-download decision in `make install-nose`."""

    def test_matching_cached_binary_skips_download(self, tmp_path: Path) -> None:
        """A binary with the pinned version and digest is reused."""
        invocation = _run_install_nose(
            tmp_path, InstallerScenario(installed_version=NOSE_VERSION)
        )

        assert invocation.result.returncode == 0, invocation.result.stderr
        assert "Verified nose" in invocation.result.stdout, (
            "A trusted cached detector must be reported as verified."
        )
        assert invocation.curl_arguments == [], (
            "A trusted cached detector must not trigger a release download."
        )

    def test_same_version_with_wrong_digest_is_reinstalled(
        self, tmp_path: Path
    ) -> None:
        """Matching version text cannot make altered executable bytes trusted."""
        invocation = _run_install_nose(
            tmp_path,
            InstallerScenario(
                installed_version=NOSE_VERSION,
                tamper_cached_binary=True,
            ),
        )

        assert invocation.result.returncode == 0, invocation.result.stderr
        assert "Installed verified nose" in invocation.result.stdout, (
            "A cached detector with altered bytes must be replaced."
        )
        assert (
            "v0.20.0/nose-cli-x86_64-unknown-linux-gnu.tar.xz"
            in (invocation.curl_arguments[0])
        ), "Reinstallation must download the pinned release archive."

    @pytest.mark.parametrize(
        ("installed_version", "case"),
        [
            pytest.param(None, "missing", id="missing-binary"),
            pytest.param("0.19.0", "mismatched", id="mismatched-version"),
        ],
    )
    def test_missing_or_stale_binary_downloads_the_pinned_release(
        self,
        tmp_path: Path,
        installed_version: str | None,
        case: str,
    ) -> None:
        """A missing or stale detector triggers a digest-checked download."""
        invocation = _run_install_nose(
            tmp_path, InstallerScenario(installed_version=installed_version)
        )

        assert invocation.result.returncode == 0, invocation.result.stderr
        assert "Installed verified nose" in invocation.result.stdout, (
            f"A {case} detector must trigger an install."
        )
        assert invocation.curl_arguments, (
            f"A {case} detector must download the pinned release archive."
        )
        assert (
            "v0.20.0/nose-cli-x86_64-unknown-linux-gnu.tar.xz"
            in (invocation.curl_arguments[0])
        ), "Installation must download the pinned detector release."

    @pytest.mark.parametrize(
        ("scenario", "diagnostic"),
        [
            pytest.param(
                InstallerScenario(archive_checksum="0" * 64),
                "archive digest mismatch",
                id="archive-digest-mismatch",
            ),
            pytest.param(
                InstallerScenario(binary_checksum="0" * 64),
                "executable digest mismatch",
                id="executable-digest-mismatch",
            ),
        ],
    )
    def test_digest_mismatch_fails_before_installing(
        self,
        tmp_path: Path,
        scenario: InstallerScenario,
        diagnostic: str,
    ) -> None:
        """Unexpected release or executable bytes are rejected before install."""
        invocation = _run_install_nose(
            tmp_path,
            scenario,
        )

        assert invocation.result.returncode != 0, (
            "An input with an unexpected digest must fail installation."
        )
        assert diagnostic in invocation.result.stderr, (
            "The installer must identify the mismatched digest."
        )
        assert not (tmp_path / "tools" / "nose").exists(), (
            "Unverified bytes must not install a detector."
        )

    def test_unsupported_platform_fails_without_downloading(
        self, tmp_path: Path
    ) -> None:
        """The installer has no source-build fallback for unpinned platforms."""
        invocation = _run_install_nose(
            tmp_path,
            InstallerScenario(system="FreeBSD"),
        )

        assert invocation.result.returncode != 0, (
            "An unsupported platform must fail closed."
        )
        assert "Unsupported nose release platform" in invocation.result.stderr, (
            "The installer must explain which platform it cannot verify."
        )
        assert invocation.curl_arguments == [], (
            "An unsupported platform must fail before downloading."
        )
