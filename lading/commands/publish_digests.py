r"""Read, validate, and compare the archive digests behind ``--expect-digests``.

A release workflow can package the workspace in a job that holds no registry
credential, record each archive's SHA-256 with ``sha256sum``, and ask a later,
credentialed ``lading publish`` to upload only those bytes. This module owns
the pure half of that contract: parsing the ``sha256sum``-format file,
checking it against the publish plan, and hashing and comparing archives. The
side effects that act on the result live in
:mod:`lading.commands.publish_archive_check`.

Every failure here is an :class:`~lading.commands.publish_errors.ExpectedDigestsError`
raised before lading stages, packages, or uploads anything, so a typo in the
file never costs a verify build.

Examples
--------
>>> text = f"{'ab' * 32}  alpha-0.1.0.crate\n\n"
>>> parse_digest_text(text, source="digests.txt")["alpha-0.1.0.crate"][:4]
'abab'
>>> archive_filename("alpha", "0.1.0")
'alpha-0.1.0.crate'
"""

import dataclasses as dc
import hashlib
import re
import typing as typ
from pathlib import Path

from lading.commands.publish_errors import ExpectedDigestsError

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from lading.commands.publish_plan import PublishPlan
    from lading.workspace import WorkspaceCrate

# ``sha256sum`` text mode writes the digest, two spaces, then the name. Crate
# archive names never contain whitespace, so a name that does is malformed.
_ENTRY_PATTERN = re.compile(r"(?P<digest>[0-9A-Fa-f]{64})  (?P<name>\S+)")
_READ_CHUNK_BYTES = 1024 * 1024
_ARCHIVE_SUFFIX = ".crate"


def archive_filename(name: str, version: str) -> str:
    """Return the archive name Cargo gives a crate: ``<name>-<version>.crate``.

    Parameters
    ----------
    name : str
        The crate's package name.
    version : str
        The crate's version.

    Returns
    -------
    str
        The file name of the packaged archive.

    Examples
    --------
    >>> archive_filename("wireframe_testing", "0.4.0-alpha1")
    'wireframe_testing-0.4.0-alpha1.crate'
    """
    return f"{name}-{version}{_ARCHIVE_SUFFIX}"


def crate_archive_filename(crate: WorkspaceCrate) -> str:
    """Return the archive file name for ``crate``."""
    return archive_filename(crate.name, crate.version)


def sha256_file(path: Path) -> str:
    r"""Return the lowercase SHA-256 hex digest of the file at ``path``.

    The file is read in bounded chunks, so a large archive is never held in
    memory whole.

    Parameters
    ----------
    path : Path
        The file to hash.

    Returns
    -------
    str
        The 64-digit lowercase hex digest.

    Raises
    ------
    OSError
        If the file cannot be read.

    Examples
    --------
    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as directory:
    ...     target = Path(directory) / "empty.crate"
    ...     _ = target.write_bytes(b"")
    ...     sha256_file(target)[:8]
    'e3b0c442'
    """  # ruff: ignore[docstring-extraneous-exception]  # raised by Path.open
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_READ_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


@dc.dataclass(frozen=True, slots=True)
class ExpectedDigests:
    """The digests a caller expects each publishable archive to have.

    Attributes
    ----------
    source : str
        Where the digests came from, for use in messages.
    digests : Mapping[str, str]
        Lowercase SHA-256 hex digests keyed by archive file name.

    Examples
    --------
    >>> expected = ExpectedDigests("d.txt", {"alpha-0.1.0.crate": "0" * 64})
    >>> expected.digest_for_filename("alpha-0.1.0.crate") == "0" * 64
    True
    """

    source: str
    digests: cabc.Mapping[str, str]

    def digest_for_filename(self, filename: str) -> str:
        """Return the expected digest for ``filename``.

        Returns
        -------
        str
            The lowercase SHA-256 hex digest.

        :func:`validate_against_plan` guarantees every publishable crate has an
        entry before any archive is checked, so a missing one is a bug and
        surfaces as :class:`KeyError`.
        """
        return self.digests[filename]

    def digest_for(self, crate: WorkspaceCrate) -> str:
        """Return the expected digest for ``crate``'s archive."""
        return self.digest_for_filename(crate_archive_filename(crate))


def _describe_lines(prefix: str, lines: cabc.Sequence[tuple[int, str]]) -> str:
    """Render offending ``(line number, text)`` pairs after ``prefix``."""
    shown = "; ".join(f"line {number}: {text!r}" for number, text in lines)
    return f"{prefix}: {shown}"


def _scan_entries(
    text: str,
) -> tuple[dict[str, str], list[tuple[int, str]], list[tuple[int, str]]]:
    """Split ``text`` into digests, malformed lines, and repeated names."""
    digests: dict[str, str] = {}
    malformed: list[tuple[int, str]] = []
    repeated: list[tuple[int, str]] = []
    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.rstrip("\r")
        if not line.strip():
            continue
        entry = _ENTRY_PATTERN.fullmatch(line)
        if entry is None:
            malformed.append((number, line))
        elif entry["name"] in digests:
            repeated.append((number, entry["name"]))
        else:
            digests[entry["name"]] = entry["digest"].lower()
    return digests, malformed, repeated


def parse_digest_text(text: str, *, source: str) -> dict[str, str]:
    """Parse ``sha256sum``-format ``text`` into digests keyed by file name.

    Blank lines are ignored. Every other line must be ``<64 hex digits>``, two
    spaces, then a file name. A file named twice is rejected even when both
    lines agree, because the file is meant to be the output of one run.

    Parameters
    ----------
    text : str
        The file's contents.
    source : str
        Where ``text`` came from, for use in error messages.

    Returns
    -------
    dict[str, str]
        Lowercase digests keyed by file name.

    Raises
    ------
    ExpectedDigestsError
        If any line is malformed or any name repeats. The message lists every
        offending line, not only the first.

    Examples
    --------
    >>> parse_digest_text("", source="d.txt")
    {}
    >>> try:
    ...     parse_digest_text("not a digest", source="d.txt")
    ... except ExpectedDigestsError as exc:
    ...     print(str(exc).startswith("Malformed digest entries in d.txt"))
    ...     print("line 1: 'not a digest'" in str(exc))
    True
    True
    """
    digests, malformed, repeated = _scan_entries(text)
    problems: list[str] = []
    if malformed:
        problems.append(
            _describe_lines(
                f"Malformed digest entries in {source} "
                "(expected '<64 hex digits>  <name>-<version>.crate')",
                malformed,
            )
        )
    if repeated:
        problems.append(
            _describe_lines(f"Duplicate digest entries in {source}", repeated)
        )
    if problems:
        raise ExpectedDigestsError("\n".join(problems))
    return digests


def load_expected_digests(path: Path) -> ExpectedDigests:
    r"""Read and parse the digest file at ``path``.

    Parameters
    ----------
    path : Path
        The ``--expect-digests`` file.

    Returns
    -------
    ExpectedDigests
        The parsed digests.

    Raises
    ------
    ExpectedDigestsError
        If the file cannot be read or decoded, or is malformed.

    Examples
    --------
    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as directory:
    ...     target = Path(directory) / "digests.txt"
    ...     _ = target.write_text("0" * 64 + "  a-1.0.0.crate\n", encoding="utf-8")
    ...     sorted(load_expected_digests(target).digests)
    ['a-1.0.0.crate']
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        message = f"Cannot read expected digests from {path}: {exc}"
        raise ExpectedDigestsError(message) from exc
    return ExpectedDigests(str(path), parse_digest_text(text, source=str(path)))


def validate_against_plan(expected: ExpectedDigests, plan: PublishPlan) -> None:
    """Require the digests to cover exactly the crates the plan will publish.

    A missing entry would leave a crate's upload unchecked, and an extra one
    means the caller recorded something other than what this run will
    publish, so either is refused before anything is staged.

    Parameters
    ----------
    expected : ExpectedDigests
        The parsed digests.
    plan : PublishPlan
        The publication plan; only its publishable crates are compared.

    Raises
    ------
    ExpectedDigestsError
        If a publishable crate has no entry, or an entry names an archive that
        is not one of the plan's. The message lists every discrepancy.

    Examples
    --------
    >>> from types import SimpleNamespace
    >>> crate = SimpleNamespace(name="a", version="1.0.0")
    >>> plan = SimpleNamespace(publishable=(crate,))
    >>> validate_against_plan(ExpectedDigests("d", {"a-1.0.0.crate": "0" * 64}), plan)
    >>> try:
    ...     validate_against_plan(ExpectedDigests("d", {}), plan)
    ... except ExpectedDigestsError as exc:
    ...     print(str(exc).endswith("no entry for a-1.0.0.crate"))
    True
    """
    planned = [crate_archive_filename(crate) for crate in plan.publishable]
    missing = [name for name in planned if name not in expected.digests]
    extra = sorted(set(expected.digests) - set(planned))
    problems = [f"no entry for {name}" for name in missing]
    problems += [f"entry for {name}, which is not in the plan" for name in extra]
    if problems:
        message = (
            f"Expected digests in {expected.source} do not match the publish "
            f"plan: {'; '.join(problems)}"
        )
        raise ExpectedDigestsError(message)


_CRATES_IO = "crates-io"


def require_crates_io_default(default_registry: str | None) -> None:
    """Refuse a default registry other than crates.io.

    ``--expect-digests`` compares published archives by downloading them from
    crates.io, so a run that would publish elsewhere cannot be checked.

    Parameters
    ----------
    default_registry : str | None
        The value of ``CARGO_REGISTRY_DEFAULT``, or ``None`` when unset.

    Raises
    ------
    ExpectedDigestsError
        If a registry other than crates.io is the default.

    Examples
    --------
    >>> require_crates_io_default(None)
    >>> require_crates_io_default("crates-io")
    >>> try:
    ...     require_crates_io_default("internal")
    ... except ExpectedDigestsError as exc:
    ...     print("'internal'" in str(exc))
    True
    """
    if default_registry in {None, "", _CRATES_IO}:
        return
    message = (
        f"--expect-digests supports crates.io only, but the default registry is "
        f"{default_registry!r}. Unset CARGO_REGISTRY_DEFAULT or drop "
        "--expect-digests"
    )
    raise ExpectedDigestsError(message)


def require_crates_io_crates(plan: PublishPlan) -> None:
    """Refuse a plan holding a crate whose manifest cannot publish to crates.io.

    A crate whose ``publish`` key lists registries without ``crates-io`` is
    meant for another registry, which ``--expect-digests`` cannot check.

    Raises
    ------
    ExpectedDigestsError
        Naming each such crate and the registries its manifest allows.
    """
    elsewhere = [
        f"{crate.name} (publish = {list(crate.registries)})"
        for crate in plan.publishable
        if crate.registries is not None and _CRATES_IO not in crate.registries
    ]
    if elsewhere:
        message = (
            "--expect-digests supports crates.io only, but these crates are "
            f"limited to other registries: {', '.join(elsewhere)}"
        )
        raise ExpectedDigestsError(message)
