"""Run the checked-in GitHub release-creation step against stand-ins."""

import json
import os
from pathlib import Path

from tests.workflow_contracts.step_harness import (
    REPOSITORY_ROOT,
    WorkflowStep,
    _find_step,
    _run_step,
    install_recording_command,
)

RELEASE_WORKFLOW = REPOSITORY_ROOT / ".github/workflows/release.yml"


def _release_create_step() -> WorkflowStep:
    """Return the checked-in create-if-absent run step."""
    return _find_step(RELEASE_WORKFLOW, "Create draft release if absent", job="release")


def test_release_creation_is_skipped_when_the_tag_exists(tmp_path: Path) -> None:
    """An existing release is reused without a create mutation."""
    record = install_recording_command(tmp_path, "gh")
    completed = _run_step(
        _release_create_step(),
        tmp_path,
        env={
            "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "COMMAND_RECORD": str(record),
            "TAG": "v1.0.0",
        },
    )

    assert completed.returncode == 0, completed.stderr
    arguments = json.loads(record.read_text(encoding="utf-8"))
    assert arguments == ["release", "view", "v1.0.0"], (
        "an existing tag should only be inspected"
    )


def test_release_creation_occurs_only_when_gh_reports_missing_tag(
    tmp_path: Path,
) -> None:
    """The draft is created only for gh's explicit release-not-found response."""
    binary = tmp_path / "bin"
    binary.mkdir()
    record = tmp_path / "gh-calls.json"
    gh = binary / "gh"
    gh.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "record = pathlib.Path(os.environ['COMMAND_RECORD'])\n"
        "argv = sys.argv[1:]\n"
        "with record.open('a', encoding='utf-8') as stream: "
        "stream.write(json.dumps(argv) + '\\n')\n"
        "if argv[:2] == ['release', 'view']:\n"
        "    sys.stderr.write(os.environ.get('GH_VIEW_ERROR', 'release not found'))\n"
        "    raise SystemExit(int(os.environ.get('GH_VIEW_STATUS', '1')))\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)

    completed = _run_step(
        _release_create_step(),
        tmp_path,
        env={
            "PATH": f"{binary}{os.pathsep}{os.environ['PATH']}",
            "COMMAND_RECORD": str(record),
            "TAG": "v1.0.0",
            "GH_VIEW_STATUS": "1",
            "GH_VIEW_ERROR": "release not found",
        },
    )

    assert completed.returncode == 0, completed.stderr
    calls = [json.loads(line) for line in record.read_text().splitlines()]
    assert len(calls) == 2, "a missing release should be inspected, then created"
    assert calls[0] == ["release", "view", "v1.0.0"], (
        "the workflow should inspect the requested tag"
    )
    assert calls[1][:3] == ["release", "create", "v1.0.0"], (
        "the workflow should create only the requested tag"
    )
    assert calls[1][3:] == ["--draft", "--generate-notes"], (
        "a new release should be created as a generated draft"
    )


def test_release_creation_fails_on_other_inspection_errors(tmp_path: Path) -> None:
    """An API failure is propagated instead of being misread as a missing tag."""
    binary = tmp_path / "bin"
    binary.mkdir()
    gh = binary / "gh"
    gh.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "sys.stderr.write(os.environ.get('GH_VIEW_ERROR', 'API unavailable'))\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)

    completed = _run_step(
        _release_create_step(),
        tmp_path,
        env={
            "PATH": f"{binary}{os.pathsep}{os.environ['PATH']}",
            "TAG": "v1.0.0",
            "GH_VIEW_ERROR": "HTTP 503",
        },
    )

    assert completed.returncode == 1, "an unexpected gh error should fail the step"
    assert "HTTP 503" in completed.stderr, "the original gh error should be preserved"
