"""Prove the staging claim across process boundaries.

The claim exists for one case that no in-process test can reach: a `lading
clean --remove` running while a *separate* `lading publish` still owns a
staged tree. The cases that turn on that therefore start a real second
process and let the operating system arbitrate.

The cases are chosen so that each fails something the others do not. A live
holder must stop the removal; a dead holder must not, because a claim that
outlived its owner would make an abandoned tree permanently unremovable,
which is the leftover `lading clean` exists to sweep; staging must claim what
it creates, without which the first case would pass while nothing in the
product claimed anything; and a tree from a release predating the claim must
stay removable.

Four more cover the moments around the claim rather than the claim itself:
that it is still held while the tree is being deleted, that a publish told to
retain its tree gives the claim up anyway, that a claim which could not be
made is reported rather than swallowed, and that a sweep judging a tree never
plants a lock of its own in it.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lading.commands import clean, publish_staging, staging_lock
from lading.commands.publish_staging import STAGING_PREFIX

#: Loads the real `staging_lock` module from its own file rather than through
#: `lading.commands`, whose package import pulls in the whole command stack for
#: four tenths of a second. These children exist to take a kernel lock on a
#: real file with the real code, and the package around it buys nothing: with
#: the import in, the burst of interpreter startups pushed an unrelated
#: Hypothesis property test past its 200 ms deadline under xdist.
_PRELUDE = """
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("staging_lock", sys.argv[2])
staging_lock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(staging_lock)
"""

#: Claims the tree named on the command line, reports that it has done so, and
#: then holds the claim open until its standard input closes. The readiness
#: line is what lets the parent proceed without sleeping: a sleep would either
#: make the test slow or make it lie on a loaded host.
_HOLDER = (
    _PRELUDE
    + """
staging_lock.claim(Path(sys.argv[1]))
sys.stdout.write("held\\n")
sys.stdout.flush()
sys.stdin.readline()
"""
)


#: Reports whether the tree named on the command line reads as in use. Run in
#: a second process because :func:`staging_lock.hold_for_removal`
#: short-circuits on the claims its own process holds, which is the answer
#: that proves nothing.
_PROBE = (
    _PRELUDE
    + """
with staging_lock.hold_for_removal(Path(sys.argv[1])) as free:
    sys.stdout.write("no\\n" if free else "yes\\n")
"""
)


def _probe_in_use(tree: Path) -> bool:
    """Ask a separate process whether ``tree`` reads as claimed.

    Returns
    -------
    bool
        What that process reported.
    """
    probe = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - explicit shell-free argv list
        [sys.executable, "-c", _PROBE, str(tree), staging_lock.__file__],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return probe.stdout.strip() == "yes"


def _staging_tree(location: Path, suffix: str) -> Path:
    """Create a staging directory the way a publish would have left one.

    Returns
    -------
    Path
        The directory created.
    """
    tree = location / f"{STAGING_PREFIX}{suffix}"
    tree.mkdir()
    (tree / "Cargo.toml").write_bytes(b"")
    return tree


def _start_holder(tree: Path) -> subprocess.Popen[str]:
    """Start a process holding ``tree``, returning once the claim is made.

    Returns
    -------
    subprocess.Popen
        The holding process, still running.
    """
    holder = subprocess.Popen(  # ruff: ignore[subprocess-without-shell-equals-true] - explicit shell-free argv list
        [sys.executable, "-c", _HOLDER, str(tree), staging_lock.__file__],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None, "the holder was started without a pipe"
    ready = holder.stdout.readline()
    assert ready.strip() == "held", f"the holder did not claim the tree: {ready!r}"
    return holder


def test_a_tree_a_live_publish_holds_is_not_removed(tmp_path: Path) -> None:
    """A staged tree in use must survive a concurrent sweep.

    This is the case the claim exists for. `clean` cannot see another
    process's `_ACTIVE_STAGING_ROOTS`, so without the claim it would delete a
    workspace copy a running publish was still reading.
    """
    tree = _staging_tree(tmp_path, "live")
    holder = _start_holder(tree)
    try:
        summary = clean.run(options=clean.CleanOptions(location=tmp_path, remove=True))
    finally:
        holder.communicate(input="\n", timeout=30)

    assert tree.is_dir(), "a tree held by a running publish was deleted"
    assert "Skipped 1" in summary, "the skipped tree was not counted"
    assert "in use by a running publish" in summary, "the reason was not reported"
    assert tree.name in summary, "the skipped tree was not named"


def test_a_tree_whose_holder_died_is_removed(tmp_path: Path) -> None:
    """A claim must not outlive the process that made it.

    The kernel drops the lock when the holder dies, so an abandoned tree is
    removable again with no stale marker to clear. A claim recorded as a
    process identifier in a file would fail here: the file would still be
    there, naming a process that is not.
    """
    tree = _staging_tree(tmp_path, "abandoned")
    holder = _start_holder(tree)
    holder.kill()
    holder.wait(timeout=30)
    assert (tree / staging_lock.LOCK_NAME).is_file(), (
        "the lock file was cleaned up, so this proves nothing about a stale one"
    )

    summary = clean.run(options=clean.CleanOptions(location=tmp_path, remove=True))

    assert not tree.exists(), "a tree whose holder had died was left behind"
    assert "Removed 1 staging directory" in summary, "the removal was not reported"


def test_a_publish_claims_the_tree_it_creates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The claim has to be made where the tree is made, or nothing holds it.

    The other cases here claim the tree themselves, so they would all pass
    against a publish that never claimed anything. This drives the staging
    code's own path instead, and asks a second process what it sees.
    """
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    created = publish_staging._normalize_build_directory(tmp_path, None)
    try:
        assert (created / staging_lock.LOCK_NAME).is_file(), (
            "staging created a tree without a lock file"
        )
        assert _probe_in_use(created), "another process did not see the claim"
    finally:
        publish_staging._remove_staged_tree(created)

    assert not created.exists(), "removing the tree left the claim holding it open"
    # Asserted on the registry rather than on the filesystem because POSIX
    # deletes a directory with an open handle inside it quite happily. The
    # release matters on Windows, where it does not, and it keeps a long-lived
    # process from accumulating a descriptor per publish.
    assert created not in staging_lock._HELD, (
        "the claim was orphaned rather than released"
    )


def test_a_tree_from_an_older_release_carries_no_claim(tmp_path: Path) -> None:
    """A leftover with no lock file at all must stay removable.

    Releases before the claim existed left trees without one, and those are
    the bulk of what this command was written to sweep.
    """
    tree = _staging_tree(tmp_path, "ancient")

    assert not (tree / staging_lock.LOCK_NAME).exists(), "the fixture claimed the tree"
    with staging_lock.hold_for_removal(tree) as free:
        assert free, "an unclaimed tree was reported in use"

    summary = clean.run(options=clean.CleanOptions(location=tmp_path, remove=True))

    assert not tree.exists(), "a tree with no claim was treated as in use"
    assert "Removed 1 staging directory" in summary, "the removal was not reported"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "Windows will not delete a directory holding an open handle, so the "
        "claim cannot be held across the removal there; what protects an "
        "in-use tree on that platform is the holder's own handle making the "
        "removal fail, which this case cannot observe"
    ),
)
def test_the_claim_is_still_held_while_the_tree_is_deleted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The removal must own the tree for the whole deletion, not just before.

    Every other case here settles the question before the removal begins, so
    all of them pass against a `clean` that asks, releases, and only then
    deletes. That order leaves a window in which a publish claims the tree and
    has its workspace deleted out from under it. This one asks a separate
    process what it sees at the instant of the deletion, which is the only
    moment that answers the question.
    """
    tree = _staging_tree(tmp_path, "held-through")
    # An unheld lock file, which is what an abandoned tree carries: `clean`
    # must be able to take it, and must then keep it.
    (tree / staging_lock.LOCK_NAME).touch()
    seen: list[bool] = []
    remove = shutil.rmtree

    def _observe(path: Path, *args: object, **kwargs: object) -> None:
        """Record what another process sees, then remove the tree for real."""
        seen.append(_probe_in_use(path))
        remove(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(shutil, "rmtree", _observe)

    summary = clean.run(options=clean.CleanOptions(location=tmp_path, remove=True))

    assert not tree.exists(), "the tree was not removed"
    assert "Removed 1 staging directory" in summary, "the removal was not reported"
    assert seen == [True], (
        "the removal claim was dropped before the tree was deleted, leaving a "
        "window in which a publish could claim it"
    )


def test_a_claim_that_cannot_be_made_is_reported(tmp_path: Path) -> None:
    """A failed claim must be a value the caller sees, not only a log line.

    The lock is a courtesy, so a filesystem that will not lock does not stop a
    publish. That decision is only defensible if the publish knows it is
    running unprotected; a `claim` returning nothing leaves every caller
    assuming the tree is held.
    """
    missing = tmp_path / "never-created"

    assert not staging_lock.claim(missing), (
        "a claim on a tree that does not exist reported success"
    )
    assert missing not in staging_lock._HELD, "a failed claim was recorded as held"


def test_judging_a_tree_never_creates_its_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A lock that vanishes mid-check must not be recreated by the sweep.

    The removal side looks for the lock file and then opens it, and the file
    can go in between, most plausibly because the tree is being removed.
    Opening it for creation there would plant a lock in a tree the sweep does
    not own and then report that tree free on the strength of a lock it had
    just made itself. The window is opened deterministically by having the
    existence check answer yes for a file that is not there.
    """
    tree = _staging_tree(tmp_path, "vanishing")
    lock = tree / staging_lock.LOCK_NAME
    real_is_file = Path.is_file
    monkeypatch.setattr(
        Path, "is_file", lambda path: path == lock or real_is_file(path)
    )

    with staging_lock.hold_for_removal(tree) as free:
        assert not free, "a tree whose lock vanished was reported free to remove"

    assert not lock.exists(), "judging the tree created a lock file inside it"


def test_a_publish_says_so_when_it_cannot_claim_its_tree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Staging must act on the claim result rather than discard it.

    The previous case proves `claim` reports the failure; this proves anything
    reads it. Without this, the return value could be deleted again and only a
    lock-level debug line would remain, which says nothing about the publish
    that is now running in a tree `clean --remove` may take.
    """
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(staging_lock, "claim", lambda _root: False)

    with caplog.at_level("WARNING", logger="lading.commands.publish_staging"):
        created = publish_staging._normalize_build_directory(tmp_path, None)

    try:
        assert created.is_dir(), "an unclaimable tree stopped the publish"
        assert "unclaimed" in caplog.text, (
            "staging did not report that its tree is unprotected"
        )
    finally:
        shutil.rmtree(created, ignore_errors=True)
