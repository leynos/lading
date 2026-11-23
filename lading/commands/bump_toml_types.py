"""Shared tomlkit table-node types for bump TOML helpers.

This leaf module keeps runtime table checks and their static type alias
consistent across the TOML traversal modules.
"""

import typing as typ

from tomlkit.container import OutOfOrderTableProxy
from tomlkit.items import InlineTable, Table

type _TableLike = Table | InlineTable | OutOfOrderTableProxy
_TABLE_LIKE_TYPES: typ.Final[
    tuple[type[Table] | type[InlineTable] | type[OutOfOrderTableProxy], ...]
] = (
    Table,
    InlineTable,
    OutOfOrderTableProxy,
)
