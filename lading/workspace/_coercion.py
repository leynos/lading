"""Workspace coercion helpers bound to :class:`WorkspaceModelError`.

The shared TOML coercion primitives live in :mod:`lading.toml_coerce`; this
module binds them to the workspace error type so the workspace builders in
:mod:`lading.workspace.graph_build` do not reach into :mod:`lading.workspace.models`
internals for them (issue #108). ``models`` defines the model types only;
``_coercion`` owns the error-bound coercion contract shared with the builders.
"""

from __future__ import annotations

import collections.abc as cabc
import functools
import typing as typ

from lading import toml_coerce
from lading.workspace.models import WorkspaceModelError

_expect_mapping = functools.partial(
    toml_coerce.expect_mapping, error=WorkspaceModelError
)


@typ.overload
def _expect_sequence(
    value: object,
    field_name: str,
    *,
    allow_none: typ.Literal[False] = False,
) -> cabc.Sequence[object]:
    """Require a sequence when ``allow_none`` is ``False``."""
    ...  # pylint: disable=unnecessary-ellipsis


@typ.overload
def _expect_sequence(
    value: object,
    field_name: str,
    *,
    allow_none: typ.Literal[True],
) -> cabc.Sequence[object] | None:
    """Allow ``None`` when ``allow_none`` is ``True``."""
    ...  # pylint: disable=unnecessary-ellipsis


def _expect_sequence(
    value: object,
    field_name: str,
    *,
    allow_none: bool = False,
) -> cabc.Sequence[object] | None:
    """Bind :func:`toml_coerce.expect_sequence` to ``WorkspaceModelError``.

    A typed wrapper (rather than ``functools.partial``) preserves the
    overloads that narrow the return type when ``allow_none`` is false.
    """
    if allow_none:
        return toml_coerce.expect_sequence(
            value, field_name, error=WorkspaceModelError, allow_none=True
        )
    return toml_coerce.expect_sequence(value, field_name, error=WorkspaceModelError)


_expect_string = functools.partial(toml_coerce.expect_string, error=WorkspaceModelError)
_is_non_empty_sequence = toml_coerce.is_non_empty_sequence
