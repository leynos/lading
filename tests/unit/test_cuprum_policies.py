"""Pin the cuprum 0.2.0 environment policy lading's subprocess contract needs.

lading's runtime runner is still subprocess-based (roadmap Phase 5.2), so
these tests exercise cuprum directly rather than through production code.
They exist because a child environment containing exactly the supplied keys
and nothing else was not expressible on cuprum 0.1.x: a complete dictionary
did not remove unlisted parent variables. It is now an opt-in mode, and each
test below fails if the opt-in is dropped or the semantics regress.

The broken-pipe half of the same contract lives in
``test_cuprum_echo_policies.py``, because it needs a FIFO handshake between
the child and the sink to be falsifiable and that machinery would otherwise
drown the environment matrix. Both modules share
:mod:`tests.helpers.cuprum_harness` rather than importing from each other.
"""

import collections.abc as cabc
import json
import typing as typ

from cuprum.context import EnvMode

from lading.runtime.subprocess_runner import normalize_environment
from lading.utils.process import c_locale_env
from tests.helpers.cuprum_harness import run_child

if typ.TYPE_CHECKING:
    import pytest

#: A parent variable that must not survive an exact child environment. Its
#: value is only meaningful in the parent, so seeing it in the child proves
#: inheritance rather than a value the test supplied.
_SENTINEL_VARIABLE = "LADING_CUPRUM_PARENT_SENTINEL"

#: Actions variables the uploader deliberately omits when a run does not
#: supply them; a CI parent would otherwise put them back.
_OMITTED_VARIABLES = ("GITHUB_REF_NAME", "GITHUB_OUTPUT")

_ENVIRONMENT_CHILD = (
    "import json, os; print(json.dumps(dict(os.environ), sort_keys=True))"
)


def _read_child_environment(
    supplied: cabc.Mapping[str, str],
    mode: EnvMode,
) -> dict[str, str]:
    """Return the environment the child reported under ``mode``."""
    result = run_child(_ENVIRONMENT_CHILD, env=supplied, env_mode=mode)
    assert result.exit_code == 0, result.stderr
    assert result.stdout is not None, "the child produced no output"
    return json.loads(result.stdout)


def test_replace_mode_passes_only_the_supplied_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replacement reproduces the runner's existing ``Popen(env=...)`` contract.

    The parent is given the sentinel and the Actions variables first, so a
    child that reports them is inheriting them rather than echoing arguments.
    """
    monkeypatch.setenv(_SENTINEL_VARIABLE, "inherited-from-parent")
    for variable in _OMITTED_VARIABLES:
        monkeypatch.setenv(variable, "set-by-the-parent")
    supplied = c_locale_env(normalize_environment({"LADING_SUPPLIED": "yes"}))

    child = _read_child_environment(supplied, EnvMode.REPLACE)

    assert set(child) == {"LADING_SUPPLIED", "LC_ALL", "LANG", "LANGUAGE"}, child
    assert child["LADING_SUPPLIED"] == "yes", child
    assert (child["LC_ALL"], child["LANG"], child["LANGUAGE"]) == ("C", "C", ""), child


def test_overlay_mode_inherits_the_parent_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default mode leaks the parent, so the replacement test can fail.

    This is the 0.1.x behaviour: a complete dictionary does not remove
    unlisted parent variables. Asserting it here keeps the neighbouring test
    falsifiable rather than vacuous.
    """
    monkeypatch.setenv(_SENTINEL_VARIABLE, "inherited-from-parent")
    for variable in _OMITTED_VARIABLES:
        monkeypatch.setenv(variable, "set-by-the-parent")
    supplied = c_locale_env(normalize_environment({"LADING_SUPPLIED": "yes"}))

    child = _read_child_environment(supplied, EnvMode.OVERLAY)

    assert child[_SENTINEL_VARIABLE] == "inherited-from-parent", child
    for variable in _OMITTED_VARIABLES:
        assert child[variable] == "set-by-the-parent", child
    assert child["LADING_SUPPLIED"] == "yes", child
