r"""The documentation preview value shared by the bump stages and output.

`DocumentationPreview` pairs the text read from a documentation file with the
text a rewrite would write. It lives in its own module so both
:mod:`lading.commands.bump_docs` (which produces previews) and
:mod:`lading.commands.bump_output` (whose `BumpChanges` carries them) can
import it at runtime. CrossHair resolves annotations at runtime, so a
type-checking-only import would silently narrow the model-checked contracts to
the empty default.

Examples
--------
>>> from pathlib import Path
>>> from lading.commands.bump_preview import DocumentationPreview
>>> preview = DocumentationPreview(
...     path=Path("README.md"), original="alpha\n", proposed="beta\n"
... )
>>> preview.proposed
'beta\n'
"""

import dataclasses as dc

# ``Path`` is imported at runtime so the annotation resolves when CrossHair
# model-checks the bump output contracts.
from pathlib import Path


@dc.dataclass(frozen=True, slots=True)
class DocumentationPreview:
    """The exact text a documentation rewrite would replace.

    A preview pairs the bytes read from disk with the bytes that would be
    written, so ``lading bump --dry-run --diff`` can show what would change
    instead of only naming files. Previews exist only for rewrites that
    report a change, so an empty tuple means "nothing to write".

    Parameters
    ----------
    path : Path
        The documentation file the previews belong to.
    original : str
        The file content as read, with newline sequences intact.
    proposed : str
        The exact content a live bump would write.
    """

    path: Path
    original: str
    proposed: str
