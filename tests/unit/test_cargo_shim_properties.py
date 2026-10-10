"""Property tests for the publish-check cargo shim's argument rewriting.

The shim decides where ``--all-features`` belongs by walking an arbitrary
``cargo`` argv: it has to recognize a leading toolchain specifier, global
options that swallow the token after them, bundled short options, the ``--``
separator, and finally the target subcommand. The example tests in
``test_cargo_shim`` pin the spellings that were reported as bugs; they cannot
say much about sequences nobody wrote down. The invariants here hold for every
argv the strategies can build, which is the part of the contract the examples
leave open.

Everything is asserted through ``rewrite_args``, the shim's one public
behaviour. Reaching into the private classifier would make the test agree with
the implementation's current shape rather than check the rewriting a caller
actually observes.
"""

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import ModuleType

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "publish-check" / "bin" / "cargo"
)
ALL_FEATURES_FLAG = "--all-features"
SEPARATOR = "--"
TARGET_SUBCOMMANDS = ("bench", "check", "clippy", "test")

#: Global options the shim knows consume the next token when spelled apart.
VALUE_TAKING_OPTIONS = (
    "--config",
    "--color",
    "--manifest-path",
    "--message-format",
    "--profile",
    "--target",
    "--target-dir",
    "--timings",
    "--jobs",
    "--unit-graph",
    "--features",
    "--exclude",
    "--future-incompat-report",
    "--filter-platform",
    "--out-dir",
)

#: Short options with the same behaviour, bundled or separate.
SHORT_VALUE_OPTIONS = ("-F", "-Z", "-j")

#: Non-target subcommands that must never gain the flag.
OTHER_SUBCOMMANDS = ("run", "build", "publish", "fmt", "doc")


def load_cargo_shim() -> ModuleType:
    """Load the publish-check cargo shim script as a fresh module.

    Returns
    -------
    ModuleType
        The shim, executed from source so the test covers the shipped file
        rather than a copy of it.

    Raises
    ------
    RuntimeError
        If the loader could not produce a module spec for ``SCRIPT_PATH``,
        which means the shim is no longer importable from where this test
        expects it.
    """
    loader = SourceFileLoader("publish_check_cargo_shim_properties", str(SCRIPT_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:  # pragma: no cover - only reachable if the file vanished
        msg = f"Failed to load cargo shim from {SCRIPT_PATH!s}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def shim() -> ModuleType:
    """Return the loaded shim for the property module."""
    return load_cargo_shim()


@st.composite
def token(draw: st.DrawFn) -> str:
    """Return one plausible cargo argument token, never the separator or flag.

    The separator and the flag are composed deliberately where a property
    needs them, so drawing them here as well would make the interesting cases
    arrive only by chance.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis draw callable.

    Returns
    -------
    str
        A token that is neither the separator nor the all-features flag.
    """
    return draw(
        st.one_of(
            st.sampled_from(TARGET_SUBCOMMANDS),
            st.sampled_from(OTHER_SUBCOMMANDS),
            st.sampled_from(VALUE_TAKING_OPTIONS),
            st.sampled_from(SHORT_VALUE_OPTIONS),
            st.from_regex(r"--[a-z][a-z-]{1,12}", fullmatch=True),
            st.from_regex(r"-[A-Za-z]{1,3}[0-9]{0,2}", fullmatch=True),
            st.from_regex(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,12}", fullmatch=True),
            st.just("+nightly"),
            st.just("+stable"),
            st.text(alphabet="abZ09._-", max_size=6).filter(
                lambda value: value not in {SEPARATOR, ALL_FEATURES_FLAG}
            ),
        )
    )


def _consumes_the_next_token(argument: str) -> bool:
    """Return whether ``argument`` spelled bare would swallow the next token.

    This is deliberately not the shim's own predicate re-exported. It is a
    property of the *grammar* being generated against: a separator is only a
    separator if the word before it is not looking for a value. Generating an
    unambiguous separator is the strategy's job, so that the properties below
    can read ``--`` as the boundary Cargo would honour.

    Parameters
    ----------
    argument : str
        The token to classify.

    Returns
    -------
    bool
        ``True`` for a value-taking long option spelled without ``=``, or a
        value-taking short option spelled without a bundled value.
    """
    if "=" in argument:
        return False
    if argument in SHORT_VALUE_OPTIONS:
        return True
    return argument in VALUE_TAKING_OPTIONS


@st.composite
def argv(draw: st.DrawFn, *, with_separator: bool) -> list[str]:
    """Return an arbitrary argv, optionally carrying a separator and its tail.

    When a separator is drawn it is placed after a head whose last token
    cannot consume it, so ``--`` is unambiguously the boundary Cargo would
    honour. A head ending in a bare ``--config`` would otherwise turn the
    separator into that option's value, and the properties that read ``--`` as
    the boundary would be asserting something false about the input rather
    than about the shim.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis draw callable.
    with_separator : bool
        Whether to append a ``--`` separator and a post-separator tail.

    Returns
    -------
    list[str]
        The argv.
    """
    head = draw(st.lists(token(), max_size=6))
    if not with_separator:
        return head
    if head and _consumes_the_next_token(head[-1]):
        head = head[:-1]
    return [*head, SEPARATOR, *draw(st.lists(token(), max_size=4))]


@given(argv(with_separator=True))
@settings(max_examples=200)
def test_arguments_after_the_separator_survive_the_rewrite_in_order(
    shim: ModuleType, generated: list[str]
) -> None:
    """A separator makes everything after it invisible to the rewrite.

    Cargo stops reading its own flags at ``--`` and hands the remainder to the
    test binary. Anything the shim read past that boundary would be a value
    the test binary never receives; anything it dropped would be worse.
    """
    separator_index = generated.index(SEPARATOR)
    tail = generated[separator_index + 1 :]

    rewritten = shim.rewrite_args(generated)

    assert rewritten.count(SEPARATOR) == 1, (
        "the shim must not introduce or remove a second separator"
    )
    rewritten_index = rewritten.index(SEPARATOR)
    assert rewritten[rewritten_index + 1 :] == tail, (
        "the post-separator arguments must be forwarded unchanged and in order"
    )


@given(argv(with_separator=True))
@settings(max_examples=200)
def test_the_flag_never_lands_after_the_separator(
    shim: ModuleType, generated: list[str]
) -> None:
    """The inserted flag is a Cargo flag, so it belongs before the separator.

    This is the bug the shim exists to avoid: appended naively, the flag
    reaches the test binary and Cargo never sees it.
    """
    rewritten = shim.rewrite_args(generated)
    separator_index = rewritten.index(SEPARATOR)

    assert ALL_FEATURES_FLAG not in rewritten[separator_index:], (
        "no --all-features may appear at or after the separator"
    )


@given(argv(with_separator=False))
@settings(max_examples=200)
def test_the_rewrite_is_idempotent(shim: ModuleType, generated: list[str]) -> None:
    """Rewriting an already-rewritten argv changes nothing further.

    A fixed point matters because the shim is invoked again for the nested
    ``cargo`` calls a test binary may make: a rewrite that added a second flag
    on the second pass would corrupt exactly those invocations.
    """
    once = shim.rewrite_args(generated)
    twice = shim.rewrite_args(once)

    assert twice == once, "the shim must reach a fixed point in one pass"


@given(
    st.one_of(st.none(), st.just("+nightly"), st.just("+stable")),
    st.text(min_size=1, max_size=6, alphabet="abcdef"),
    st.lists(
        st.one_of(
            st.just(ALL_FEATURES_FLAG),
            st.from_regex(r"--[a-z][a-z-]{1,10}=[a-z0-9]{1,6}", fullmatch=True),
            st.from_regex(r"-[jZF][0-9]{1,2}", fullmatch=True),
        ),
        max_size=3,
    ),
)
@settings(max_examples=200)
def test_a_target_subcommand_followed_by_a_plain_word_gains_the_flag(
    shim: ModuleType, toolchain: str | None, word: str, trailing: list[str]
) -> None:
    """A post-subcommand word is an argument, not a second command.

    ``cargo test foo`` still runs a test; the word after the target does not
    end the invocation, so the flag must still be inserted before it. The
    prefix is restricted to a toolchain specifier and to self-contained flags
    on purpose: a separate value-taking option such as ``--target`` would make
    the following word *its* value, and a non-target subcommand such as ``run``
    would legitimately suppress the flag, since the words after it name a
    binary rather than a Cargo command.
    """
    prefix = [] if toolchain is None else [toolchain]
    generated = [*prefix, "test", word, *trailing]

    rewritten = shim.rewrite_args(generated)

    assert ALL_FEATURES_FLAG in rewritten, (
        "a word following the target subcommand must not suppress the flag"
    )
    assert len(rewritten) in {len(generated), len(generated) + 1}, (
        "the shim inserts at most one token"
    )


@given(
    st.sampled_from(TARGET_SUBCOMMANDS),
    st.lists(token(), max_size=4),
)
@settings(max_examples=200)
def test_a_bare_target_subcommand_gains_the_flag(
    shim: ModuleType, subcommand: str, extra: list[str]
) -> None:
    """Each target subcommand, with arbitrary trailing words, gains the flag.

    The word after the subcommand is part of the same invocation, not a new
    command, so it must not suppress the flag.
    """
    generated = [subcommand, *extra]

    rewritten = shim.rewrite_args(generated)

    assert rewritten.count(ALL_FEATURES_FLAG) == 1, (
        f"{subcommand} must receive exactly one --all-features flag"
    )


@given(
    st.sampled_from(OTHER_SUBCOMMANDS),
    st.lists(token(), max_size=4),
)
@settings(max_examples=200)
def test_a_non_target_subcommand_never_gains_the_flag(
    shim: ModuleType, subcommand: str, extra: list[str]
) -> None:
    """A subcommand the shim does not target is returned verbatim."""
    generated = [subcommand, *extra]

    assert shim.rewrite_args(generated) == generated, (
        f"a {subcommand} invocation must pass through untouched"
    )


@given(
    st.sampled_from(VALUE_TAKING_OPTIONS),
    st.lists(token(), max_size=3),
)
@settings(max_examples=200)
def test_a_value_taking_option_does_not_hide_a_later_subcommand(
    shim: ModuleType, option: str, tail: list[str]
) -> None:
    """A global option's value is consumed, so a target after it is still found.

    The option and its value occupy two positions; a walker that advanced by
    one would read the value as the subcommand and then treat the real target
    as an argument to it.
    """
    generated = [option, "value-for-the-option", "test", *tail]

    rewritten = shim.rewrite_args(generated)

    assert ALL_FEATURES_FLAG in rewritten, (
        f"{option} must consume its value so that the following `test` is "
        f"still recognized as the target subcommand"
    )
    assert rewritten[:2] == [option, "value-for-the-option"], (
        "the option and its value must be preserved in place"
    )
