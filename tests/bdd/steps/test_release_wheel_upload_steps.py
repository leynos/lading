"""BDD steps for the standalone release wheel upload.

The feature pins the two things a wheel upload cannot get wrong: that
``uv run --script`` builds an environment holding the same cuprum the
repository pins, and that ``gh`` is only ever the recording stub. Both are
properties of a child process, so every step here observes a real run rather
than a double.

Each scenario is bound with its own ``@scenario`` decorator instead of the
``scenarios()`` helper, because one of them needs its own marker: the pin
assertion is red until task 5.1.4 selects the beta.
"""

from __future__ import annotations

import importlib.metadata
import typing as typ
from pathlib import Path

import pytest
from pytest_bdd import given, scenario, then, when

from tests.helpers.gh_stub import (
    SENTINEL_VARIABLE,
    STUB_TAG,
    GhStub,
    install_gh_stub,
    isolated_environment,
    run_uploader,
)
from tests.workflow_contracts.test_cuprum_selection import _declared_pin

if typ.TYPE_CHECKING:
    import subprocess

pytestmark = pytest.mark.timeout(60)

_FEATURES_DIR = Path(__file__).resolve().parent.parent / "features"
_FEATURE = str(_FEATURES_DIR / "release_wheel_upload.feature")

PYPROJECT = Path(__file__).resolve().parents[3] / "pyproject.toml"

#: The wheel the Background step creates, and the exact upload argument list
#: the single successful call must carry.
_WHEEL_NAME = "lading-0.0.0-py3-none-any.whl"
_RELEASE_PREFIX = ("release", "upload", STUB_TAG)


def _error_line(stderr: str) -> str:
    """Return the uploader's own ``Error:`` line from ``stderr``.

    Parameters
    ----------
    stderr : str
        The child's captured stderr.

    Returns
    -------
    str
        The reported failure line.

    Raises
    ------
    AssertionError
        If the uploader reported no error line.
    """
    for line in stderr.splitlines():
        if line.startswith("Error: "):
            return line
    message = f"no uploader error line in {stderr!r}"
    raise AssertionError(message)


def _site_packages(virtual_env: str) -> list[Path]:
    """Return the ``site-packages`` directories of one environment.

    The interpreter version is part of the path, so it is discovered rather
    than assumed: the standalone environment is built by uv and need not be
    the same version this test runs under.

    Returns
    -------
    list[Path]
        The matching ``site-packages`` directories. Empty if the environment
        has none, which is left for the caller to report.
    """
    return list(Path(virtual_env).glob("lib/python*/site-packages"))


def _cuprum_version_under(virtual_env: str) -> str:
    """Return the cuprum version installed in the environment at ``virtual_env``.

    Parameters
    ----------
    virtual_env : str
        The environment the child reported.

    Returns
    -------
    str
        The version of cuprum that environment holds.

    Raises
    ------
    AssertionError
        If that environment holds no cuprum, or holds more than one. Either
        means the question could not be answered, which must not read as a
        version mismatch.
    """
    site = _site_packages(virtual_env)
    distributions = list(importlib.metadata.distributions(name="cuprum", path=site))
    if len(distributions) != 1:
        message = f"expected one cuprum distribution under {virtual_env}, found {site}"
        raise AssertionError(message)
    return distributions[0].version


@given(
    'a dist directory containing "lading-0.0.0-py3-none-any.whl"',
    target_fixture="dist_directory",
)
def given_a_dist_directory(tmp_path: Path) -> Path:
    """Create the directory the uploader will search.

    Returns
    -------
    Path
        The directory holding the one wheel.
    """
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / _WHEEL_NAME).write_bytes(b"")
    return dist


@given("a recording gh stub that exits 0", target_fixture="gh_stub")
def given_a_recording_stub(tmp_path: Path) -> GhStub:
    """Install a stub ``gh`` that succeeds."""
    return install_gh_stub(tmp_path)


@given(
    'a recording gh stub that writes "HTTP 422: asset exists" to stderr and exits 1',
    target_fixture="gh_stub",
)
def given_a_rejecting_stub(tmp_path: Path) -> GhStub:
    """Install a stub ``gh`` that rejects the upload with GitHub's diagnostic."""
    return install_gh_stub(tmp_path, exit_code=1, stderr="HTTP 422: asset exists\n")


@given(
    'the parent environment holds a GH_TOKEN and the sentinel "LADING_STUB_SENTINEL"'
)
def given_the_parent_holds_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Put a credential in the parent, for the child to fail to inherit.

    The token is set here rather than assumed absent so the assertion is
    meaningful: a run that inherited everything would carry it through, and a
    helper that merely never set one would look identical.
    """
    monkeypatch.setenv("GH_TOKEN", "ghp_this_must_not_reach_the_child")
    monkeypatch.setenv(SENTINEL_VARIABLE, "the-child-inherited-the-environment")


@when(
    'the uploader runs standalone for tag "v0.0.0-stub"',
    target_fixture="completed",
)
def when_the_uploader_runs(
    tmp_path: Path, gh_stub: GhStub
) -> subprocess.CompletedProcess[str]:
    """Run the uploader through ``uv run --script``, as the workflow does.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The completed run.
    """
    environment = isolated_environment(
        gh_stub,
        sentinel="the-child-inherited-the-environment",
        extra={"GITHUB_REF_NAME": STUB_TAG},
    )
    return run_uploader(
        "standalone", environment, "--directory", str(tmp_path / "dist")
    )


@then("the uploader exits 0")
def then_the_uploader_succeeds(completed: subprocess.CompletedProcess[str]) -> None:
    """Assert the run succeeded."""
    assert completed.returncode == 0, completed.stderr


@then("the uploader exits 1")
def then_the_uploader_fails(completed: subprocess.CompletedProcess[str]) -> None:
    """Assert the run failed."""
    assert completed.returncode == 1, completed.stderr


@then(
    'gh was called exactly once with "release upload v0.0.0-stub" and the wheel '
    'and "--clobber"'
)
def then_gh_was_called_once(gh_stub: GhStub, dist_directory: Path) -> None:
    """Assert the exact argv list of the single recorded call.

    The whole list is compared rather than its prefix, so a second upload of
    the same asset -- which GitHub would reject, and ``--clobber`` would hide
    -- cannot pass unnoticed.
    """
    calls = gh_stub.calls()
    assert len(calls) == 1, f"expected one call, recorded {calls}"
    expected = (*_RELEASE_PREFIX, str(dist_directory / _WHEEL_NAME), "--clobber")
    assert calls[0].argv == expected, f"gh received {calls[0].argv}"


@then("the environment that ran holds the cuprum version pinned in pyproject.toml")
def then_the_environment_holds_the_pin(gh_stub: GhStub) -> None:
    """Assert the standalone environment's cuprum equals the repository pin.

    The version is read from the environment that actually ran, resolved
    through the ``VIRTUAL_ENV`` the child reported, rather than from the test
    interpreter: those are different environments, and only the child's
    answers the question the release workflow asks.
    """
    expected = _declared_pin(PYPROJECT.read_text(encoding="utf-8"))
    calls = gh_stub.calls()
    assert calls, "the uploader never reached gh, so no environment was recorded"
    virtual_env = calls[0].virtual_env
    assert virtual_env is not None, (
        "the child reported no VIRTUAL_ENV, so its environment cannot be inspected"
    )
    installed = _cuprum_version_under(virtual_env)
    assert installed == expected, (
        f"the standalone environment holds cuprum {installed}, but pyproject.toml "
        f"pins {expected}"
    )


@then('the uploader\'s error line contains "HTTP 422: asset exists"')
def then_the_error_line_reports_the_diagnostic(
    completed: subprocess.CompletedProcess[str],
) -> None:
    """Assert gh's diagnostic reaches the uploader's own error line.

    The assertion is made on that line rather than on all of stderr: the stub
    writes its diagnostic to inherited stderr too, so a search of the whole
    stream would pass even if the command boundary had discarded it.
    """
    reported = _error_line(completed.stderr)
    assert "HTTP 422: asset exists" in reported, reported


@then('gh saw the sentinel "LADING_STUB_SENTINEL"')
def then_gh_saw_the_sentinel(gh_stub: GhStub) -> None:
    """Assert the child inherited the environment, as production needs."""
    calls = gh_stub.calls()
    assert calls, "the uploader never reached gh"
    sentinel = calls[0].sentinel
    assert sentinel == "the-child-inherited-the-environment", (
        f"the child did not inherit the environment: saw {sentinel!r}"
    )


@then("gh saw no GH_TOKEN or GITHUB_TOKEN and an empty GH_CONFIG_DIR")
def then_gh_saw_no_credentials(gh_stub: GhStub) -> None:
    """Assert neither credential reached the child, and its config is empty.

    The parent holds ``GH_TOKEN``, so this compares the child's record with a
    token that really was set: a helper that simply never exported one would
    pass an absence check without protecting anything.
    """
    calls = gh_stub.calls()
    assert calls, "the uploader never reached gh"
    call = calls[0]
    assert not call.saw_gh_token, "GH_TOKEN reached the child, which must never happen"
    assert not call.saw_github_token, "GITHUB_TOKEN reached the child"
    assert call.gh_config_dir is not None, "the child kept the machine's gh login"
    config = Path(call.gh_config_dir)
    assert not list(config.iterdir()), f"{config} is not an empty directory"


@then('no recorded gh call is "release create" or "release edit"')
def then_no_call_mutates_a_release(gh_stub: GhStub) -> None:
    """Assert the uploader never changed the release itself.

    ``release create`` and ``release edit`` are the two calls that would
    publish, re-target, or annotate a real release. ``--clobber`` on a real
    host is the other half of the same hazard, which is why the stub exists.
    """
    mutations = [call for call in gh_stub.calls() if call.is_release_mutation]
    assert not mutations, f"the uploader mutated a release: {mutations}"


# The marker goes above ``@scenario``: that decorator replaces the function it
# is given, so a mark applied underneath it is discarded and the test would run
# unmarked.
@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="5.1.4: cuprum beta not yet selected",
)
@scenario(_FEATURE, "The standalone uploader attaches a wheel with the pinned cuprum")
def test_standalone_upload_uses_the_pinned_cuprum() -> None:
    """The standalone path runs under the version the repository pins."""


@scenario(_FEATURE, "A rejected upload reports gh's diagnostic")
def test_a_rejected_upload_reports_the_diagnostic() -> None:
    """A rejected asset keeps GitHub's reason on the uploader's error line."""


@scenario(_FEATURE, "gh inherits the environment but never credentials")
def test_gh_inherits_the_environment_but_no_credentials() -> None:
    """The stub sees the sentinel and no credential, and mutates no release."""
