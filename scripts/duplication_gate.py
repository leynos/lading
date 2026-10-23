#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.14"
# dependencies = ["cyclopts==4.25.2", "tomlkit==0.15.1"]
# ///
"""Blocking code-duplication gate over the pinned nose detector.

The gate runs the pinned ``nose`` binary with the repository's ``[tool.nose]``
settings, removes families covered by reasoned ``[tool.duplication_gate]``
allow entries, and fails while unsuppressed families remain. Stale allow
entries are reported so that resolved duplication does not leave dead
configuration behind.

``scripts/duplication_allowlist.py`` documents the allow-entry key syntax
and matching rules; ``scripts/nose_detector.py`` owns the detector itself.

The wrapper runs on an explicitly selected CPython 3.14, not the project
virtualenv: it is tooling, and must not need to import ``lading`` or its
dependencies to lint the repository.
"""

import json
import os
import sys
import tomllib
from collections import abc as cabc
from pathlib import Path

import cyclopts
from duplication_allowlist import (
    AllowEntry,
    append_allow_entry,
    load_allowlist,
    validate_key,
)
from nose_detector import (
    PYPROJECT,
    NoseEnvironment,
    discover_binary,
    load_settings,
    run_detector,
)
from nose_schema import Finding, GateConfigError, GateExecutionError

app = cyclopts.App(help="Run or configure the code-duplication gate.")

type AllowlistReader = cabc.Callable[[Path], tuple[AllowEntry, ...]]
type FindingDetector = cabc.Callable[[], list[Finding]]


def _matching_allow_positions(
    finding: Finding,
    allowlist: cabc.Sequence[AllowEntry],
) -> set[int]:
    """Return the indexes of allow entries covering one finding."""
    return {
        position for position, entry in enumerate(allowlist) if entry.matches(finding)
    }


def _stale_allow_entries(
    allowlist: cabc.Sequence[AllowEntry],
    used_positions: set[int],
) -> list[AllowEntry]:
    """Return entries that did not match any finding in the scan."""
    return [
        entry
        for position, entry in enumerate(allowlist)
        if position not in used_positions
    ]


def partition_findings(
    findings: cabc.Sequence[Finding],
    allowlist: cabc.Sequence[AllowEntry],
) -> tuple[list[Finding], list[Finding], list[AllowEntry]]:
    """Split findings into blocking and allowed, and spot stale entries.

    Parameters
    ----------
    findings : collections.abc.Sequence[Finding]
        Normalized detector findings.
    allowlist : collections.abc.Sequence[AllowEntry]
        Reasoned exceptions from the repository configuration.

    Returns
    -------
    tuple[list[Finding], list[Finding], list[AllowEntry]]
        Blocking findings, covered findings, and entries that no longer match
        any finding in the selected scan surface.
    """
    blocking: list[Finding] = []
    allowed: list[Finding] = []
    used_positions: set[int] = set()
    for finding in findings:
        matches = _matching_allow_positions(finding, allowlist)
        used_positions.update(matches)
        (allowed if matches else blocking).append(finding)
    stale = _stale_allow_entries(allowlist, used_positions)
    return blocking, allowed, stale


def detect_findings() -> list[Finding]:
    """Run the pinned detector with the repository's ``[tool.nose]`` settings.

    Returns
    -------
    list[Finding]
        Normalized duplication families emitted by the pinned detector.

    Notes
    -----
    Configuration and execution errors from the settings loader and detector
    propagate to the CLI boundary.
    """
    return run_detector(
        load_settings(PYPROJECT),
        environment=NoseEnvironment(
            repository_root=PYPROJECT.parent,
            binary_override=os.environ.get("NOSE_BIN"),
            search_path=os.environ.get("PATH"),
        ),
        binary_finder=discover_binary,
    )


def _report(
    blocking: list[Finding], allowed: list[Finding], stale: list[AllowEntry]
) -> None:
    """Print stale, blocking, or passing sections in a stable order."""
    _report_stale_entries(stale)
    if blocking:
        _report_blocking_findings(blocking)
        return
    _report_passed(allowed)


def _report_stale_entries(stale: list[AllowEntry]) -> None:
    """Print unmatched entries with the ranked-surface qualification."""
    for entry in stale:
        joined = " ~ ".join(entry.keys)
        print(f"stale allow entry ({joined}): unmatched in this scan ({_SURFACE_HINT})")


def _report_passed(allowed: list[Finding]) -> None:
    """Print the passing result and the number of reviewed exceptions."""
    suffix = f"; {len(allowed)} allowed by reasoned exceptions" if allowed else ""
    print(f"duplication gate passed{suffix}")


def _report_blocking_findings(blocking: list[Finding]) -> None:
    """Print blocking families with the extraction or exception guidance."""
    print(f"duplicate code: {len(blocking)} unsuppressed family/families")
    for finding in blocking:
        print(f"  {finding.label} ({finding.witness}, value {finding.value:.1f})")
    print(
        "Extract the shared logic into one helper, or record a considered "
        "exception:\n  make duplication-allow FIRST='<path[::name]>' "
        "[SECOND='<path[::name]>'] REASON='<why this stays>'"
    )


#: The ranked surface is capped, so absence from it proves only that no
#: current finding matched, not that the duplication is gone.
_SURFACE_HINT = (
    "verify the source before removing it; the ranked view is capped, so "
    "absence here is not proof the duplication is gone"
)


def _read_allowlist(allowlist_reader: AllowlistReader) -> tuple[AllowEntry, ...]:
    """Read the reasoned allowlist, naming unreadable configuration."""
    try:
        return allowlist_reader(PYPROJECT)
    except (OSError, tomllib.TOMLDecodeError) as error:
        # GateConfigError needs no re-raise guard here: it is a ValueError
        # *sibling* of TOMLDecodeError rather than a subclass, so this clause
        # never intercepts it and the reader's own diagnostic propagates.
        msg = f"cannot load duplication allowlist: {error}"
        raise GateExecutionError(msg) from error


def _detect_findings(detector: FindingDetector) -> list[Finding]:
    """Run the detector, separating execution failures from bad configuration."""
    try:
        return detector()
    except GateConfigError:
        raise
    except OSError as error:
        msg = f"nose detector failed: {error}"
        raise GateExecutionError(msg) from error
    except (TypeError, ValueError) as error:
        raise GateConfigError(str(error)) from error


def _check_inputs(
    *,
    allowlist_reader: AllowlistReader,
    detector: FindingDetector,
) -> tuple[tuple[AllowEntry, ...], list[Finding]]:
    """Load the gate inputs with explicit local-environment failures."""
    return (_read_allowlist(allowlist_reader), _detect_findings(detector))


@app.command
def check() -> None:
    """Run the blocking duplication gate and exit non-zero on findings.

    The command reads the repository's fixed ``pyproject.toml`` path and the
    detector executes with an explicit repository working directory; it does
    not mutate the caller's process directory.

    Raises
    ------
    SystemExit
        With status 1 for blocking findings or status 2 for malformed
        configuration.
    """
    try:
        allowlist, findings = _check_inputs(
            allowlist_reader=load_allowlist,
            detector=detect_findings,
        )
    except GateConfigError as error:
        print(f"configuration error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
    blocking, allowed, stale = partition_findings(findings, allowlist)
    _report(blocking, allowed, stale)
    if blocking:
        raise SystemExit(1)


def _decode_second_keys(
    second: list[str] | None,
    second_keys_json: str | None,
) -> tuple[str, ...]:
    """Resolve repeated CLI keys or one JSON-array key argument."""
    if second is not None:
        if second_keys_json is not None:
            msg = "SECOND and SECOND_KEYS_JSON cannot be supplied together"
            raise GateConfigError(msg)
        return tuple(second)
    if second_keys_json is None:
        return ()
    return _decode_json_key_array(second_keys_json)


def _decode_json_key_array(encoded: str) -> tuple[str, ...]:
    """Decode and validate the JSON form of additional location keys."""
    try:
        decoded = json.loads(encoded)
    except json.JSONDecodeError as error:
        msg = "SECOND_KEYS_JSON must be a JSON array of strings"
        raise GateConfigError(msg) from error
    if not isinstance(decoded, list) or not all(
        isinstance(key, str) for key in decoded
    ):
        msg = "SECOND_KEYS_JSON must be a JSON array of strings"
        raise GateConfigError(msg)
    return tuple(decoded)


@app.command
def allow(
    *,
    first: str,
    second: list[str] | None = None,
    second_keys_json: str | None = None,
    reason: str,
) -> None:
    """Record one reasoned exception in ``[tool.duplication_gate]``.

    Parameters
    ----------
    first : str
        Location key (``path`` or ``path::name``) of the first or only member.
    second : list[str] | None
        Further location keys; when supplied, the entry silences only families
        whose every location matches one of the listed keys.
    second_keys_json : str | None
        A JSON array of further location keys for callers such as Make that
        need to pass several keys through one safely quoted argument.
    reason : str
        Reviewable justification for keeping the duplication.

    Raises
    ------
    SystemExit
        If the reason is empty or a location key is malformed.
    """
    if not reason.strip():
        print("REASON must not be empty", file=sys.stderr)
        raise SystemExit(2)
    try:
        keys = (first, *_decode_second_keys(second, second_keys_json))
        for key in keys:
            validate_key(key, context=f"'{key}'")
        append_allow_entry(PYPROJECT, keys=keys, reason=reason)
    except (GateConfigError, OSError) as error:
        print(f"configuration error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
    print(f"recorded duplication exception for {' ~ '.join(keys)}")


if __name__ == "__main__":
    app()
