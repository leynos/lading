"""Parse a workflow with a loader that refuses a key declared twice.

PyYAML keeps the last of two identical keys without a word, so a job declaring
`runs-on` or `uses` twice would read as whichever came second and a contract
could pass on the half that GitHub discards. GitHub rejects such a workflow, so
refusing it here costs nothing. Contracts that read workflows by hand use
`_load` here instead of `yaml.safe_load`.
"""

import yaml
import yaml.resolver

type Workflow = dict[object, object]


class DuplicateKeyError(ValueError):
    """Raised when a workflow declares one mapping key twice."""


class _StrictLoader(yaml.SafeLoader):
    """A safe loader that refuses a key declared twice in one mapping."""


def _construct_mapping(loader: _StrictLoader, node: yaml.MappingNode) -> Workflow:
    """Build a mapping, raising on a repeated key.

    Returns
    -------
    Workflow
        The constructed mapping.

    Raises
    ------
    DuplicateKeyError
        If one key appears twice.

    """
    seen: set[object] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in seen:
            message = f"duplicate key {key!r} at {key_node.start_mark}"
            raise DuplicateKeyError(message)
        seen.add(key)
    return loader.construct_mapping(node, deep=True)


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)


def _load(text: str) -> object:
    """Parse one YAML document with the strict safe loader.

    Returns
    -------
    object
        The parsed document.

    """
    loader = _StrictLoader(text)
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()
