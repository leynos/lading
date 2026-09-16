"""Cover what `publish.run` says about the staged tree it names.

The summary line is built inside the `staged_workspace` block and returned
after it, so by the time a caller reads `Staged workspace at: <path>` the
tree has usually gone: cleanup has defaulted to on since issue #269. These
tests pin the reported state to the option that decides it. The plan
snapshot cannot: it redacts the whole line, so it passes whichever state the
line reports.
"""

from __future__ import annotations

import typing as typ

from lading.commands import publish

from .conftest import make_config, make_crate, make_workspace

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _staging_line(output: str) -> str:
    """Return the summary line naming the staged workspace.

    Returns
    -------
    str
        The line beginning with the staged-workspace prefix.

    Raises
    ------
    AssertionError
        If the output holds no such line, which would mean the summary
        stopped naming the staged tree at all.
    """
    for line in output.splitlines():
        if line.startswith("Staged workspace at:"):
            return line
    message = f"no staged-workspace line in publish output: {output!r}"
    raise AssertionError(message)


def _run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, cleanup: bool) -> str:
    """Run a publish against a one-crate workspace and return its summary.

    The build directory is named so the staged tree stays under ``tmp_path``
    rather than the system temporary directory, which keeps the retained case
    from leaking a tree the suite's leak check would then fail on.

    Returns
    -------
    str
        The publish summary.
    """
    root = tmp_path.resolve()
    workspace = make_workspace(root, make_crate(root, "alpha"))
    monkeypatch.setattr("lading.workspace.load_workspace", lambda _: workspace)
    return publish.run(
        root,
        make_config(),
        options=publish.PublishOptions(
            build_directory=tmp_path.parent / "build", cleanup=cleanup
        ),
    )


def test_the_summary_says_a_removed_tree_has_gone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cleanup is the default, so the common case must report the removal.

    Naming a path with nothing else said reads as an invitation to go and
    look at a directory that publish deleted on its way out.
    """
    output = _run(monkeypatch, tmp_path, cleanup=True)

    assert _staging_line(output).endswith("(removed)"), (
        "a publish that removed its staged tree did not say so"
    )


def test_the_summary_leaves_a_retained_tree_unqualified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A tree kept for debugging is still there, so nothing is appended.

    This is what stops the previous case passing against a call site that
    reported removal unconditionally.
    """
    output = _run(monkeypatch, tmp_path, cleanup=False)

    assert not _staging_line(output).endswith("(removed)"), (
        "a retained staged tree was reported as removed"
    )
