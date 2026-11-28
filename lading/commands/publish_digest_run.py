"""Digest-related steps of the ``lading publish`` workflow.

``--expect-digests`` adds work around the main publish sequence: choosing a
runner that hides registry credentials, validating the digest file and the
registry before the pre-flight, and refusing an alternate default registry at
the staged paths. They live here so :mod:`lading.commands.publish` stays the
readable outline of the run, and so this module can be tested apart from it.
"""

import collections.abc as cabc
import contextlib
import typing as typ

from lading.commands import publish_cargo_config, publish_digests, publish_pipeline
from lading.commands.publish_archive_check import ArchiveVerifier, create_verifier
from lading.commands.publish_execution import redacting_invoke
from lading.commands.publish_staging import _resolve_staged_crate_root
from lading.runtime.redacting_runner import redacting_runner
from lading.utils.secret_scope import SecretScope, use_secrets

if typ.TYPE_CHECKING:
    import pathlib

    from lading.commands.publish import PublishOptions
    from lading.commands.publish_plan import PublishPlan
    from lading.runtime import CommandRunner


def discovery_runner(
    options: PublishOptions, scope: SecretScope | None
) -> CommandRunner | None:
    """Return the runner for workspace discovery, only when digests are expected.

    Without digests discovery keeps its ambient runner, so behaviour is as
    before. With them it uses the injected runner, or one that hides the
    caller's registry tokens, so ``cargo metadata`` output cannot leak one.
    ``scope`` is the run's open secret scope, which that runner extends with
    the tokens each command brings.

    Returns
    -------
    CommandRunner | None
        The runner to install around discovery, or ``None`` for the ambient one.
    """
    environment = options.cargo_environment
    if scope is None or environment is None:
        return None
    return options.command_runner or redacting_runner(
        scope, cargo_home=environment.cargo_home
    )


def refuse_staged_defaults(
    plan: PublishPlan, staging_root: pathlib.Path, options: PublishOptions
) -> None:
    """Refuse an alternate default registry that only the staged paths see.

    Cargo runs from the staged crate roots, whose ancestors (the staging
    directory's parents) differ from the checkout's, so configuration there is
    invisible to the check made before staging. This runs after staging and
    before anything is packaged.
    """
    if options.expect_digests is None or options.cargo_environment is None:
        return
    starts = [("the staged workspace", staging_root)]
    starts += [
        (
            f"staged crate {crate.name}",
            _resolve_staged_crate_root(crate, plan, staging_root),
        )
        for crate in plan.publishable
    ]
    publish_cargo_config.refuse_alternate_default_at(starts, options.cargo_environment)


def command_runner(options: PublishOptions, scope: SecretScope | None) -> CommandRunner:
    """Return the injected runner, or the default one.

    With digests expected (``scope`` is the run's open secret scope) the
    default runner hides the caller's registry tokens from subprocess output,
    so a programmatic caller gets the same protection as the command line.

    Returns
    -------
    CommandRunner
        The runner for this publish.
    """
    if options.command_runner is not None:
        return options.command_runner
    environment = options.cargo_environment
    if scope is None or environment is None:
        return publish_pipeline._invoke
    return redacting_invoke(scope, cargo_home=environment.cargo_home)


def digest_checks(
    options: PublishOptions,
    plan_for: cabc.Callable[[], PublishPlan],
    runner: CommandRunner,
    workspace_root: pathlib.Path,
) -> tuple[PublishPlan | None, ArchiveVerifier | None]:
    """Validate the digests and registry before the pre-flight, if they are used.

    Returns
    -------
    tuple[PublishPlan | None, ArchiveVerifier | None]
        The plan and verifier when digests are expected, otherwise ``(None,
        None)`` and the caller plans later.
    """
    # Read before the pre-flight, which builds the workspace: a typo in the
    # digest file should cost seconds, not a verify build.
    expected = load_expected_digests(options, workspace_root)
    if expected is None or options.cargo_environment is None:
        return None, None
    plan = plan_for()
    publish_cargo_config.refuse_alternate_default(
        plan, workspace_root, options.cargo_environment
    )
    return plan, archive_verifier(expected, plan, options, runner)


def load_expected_digests(
    options: PublishOptions, workspace_root: pathlib.Path
) -> publish_digests.ExpectedDigests | None:
    """Read the ``--expect-digests`` file, if one was given, before any work."""
    if options.expect_digests is None:
        return None
    return publish_digests.load_expected_digests(
        workspace_root / options.expect_digests
    )


def archive_verifier(
    expected: publish_digests.ExpectedDigests,
    plan: PublishPlan,
    options: PublishOptions,
    runner: CommandRunner,
) -> ArchiveVerifier:
    """Check the digests against the plan and build the run's verifier."""
    publish_digests.validate_against_plan(expected, plan)
    publish_digests.require_crates_io_crates(plan)
    return create_verifier(expected, runner, options.published_digest_fetcher)


def hidden_secrets(
    options: PublishOptions,
) -> contextlib.AbstractContextManager[SecretScope | None]:
    """Hide the caller's registry tokens from shown messages for a digest run.

    Returns
    -------
    contextlib.AbstractContextManager[SecretScope | None]
        :func:`~lading.utils.secret_scope.use_secrets` for the known secrets,
        yielding the scope the run's runners extend, or a no-op yielding
        ``None`` when digests are not expected.
    """
    environment = options.cargo_environment
    if options.expect_digests is None or environment is None:
        return contextlib.nullcontext()
    return use_secrets(environment.secrets)


def archive_summary(verifier: ArchiveVerifier) -> tuple[str, ...]:
    """Render the verifier's outcomes for the publish summary.

    Returns
    -------
    tuple[str, ...]
        A header naming the digest file, then one line per decided crate.
    """
    header = f"Archive checks against {verifier.expected.source}:"
    lines = (
        f"- {decided.crate} {decided.version}: {decided.outcome.value} "
        f"({decided.detail})"
        for decided in verifier.outcomes
    )
    return (header, *lines)
