"""``publish.run`` hands callers sanitized exceptions, traceback included."""

import traceback
from pathlib import Path

import pytest

from lading import config as config_module
from lading.commands import publish
from lading.commands.publish_cargo_config import CargoEnvironment
from lading.commands.publish_errors import ExpectedDigestsError
from lading.utils.redaction import redact_text
from tests.helpers.publish_digests import PublishRig

_LEAK = "exc-credential-44dd55ee66ff"


def _redact(text: str) -> str:
    """Replace the synthetic token in ``text``."""
    return redact_text(text, (_LEAK,))


def test_malformed_digest_line_reaches_the_caller_without_the_token(
    tmp_path: Path,
) -> None:
    """A digest-file error that quotes the line is clean, traceback included."""
    rig = PublishRig.create(tmp_path)
    digests = rig.root / "digests.txt"
    digests.write_text(f"not a digest {_LEAK}\n", encoding="utf-8")
    options = publish.PublishOptions(
        build_directory=rig.build_directory,
        command_runner=rig.cargo,
        expect_digests=digests,
        cargo_environment=CargoEnvironment(None, None, (_LEAK,)),
    )

    with pytest.raises(ExpectedDigestsError) as excinfo:
        publish.run(
            rig.root,
            config_module.LadingConfig(),
            rig.workspace(),
            options=options,
        )

    assert "Malformed digest entries" in str(excinfo.value), (
        "the failure must still be reported, with its type"
    )
    assert _LEAK not in "".join(traceback.format_exception(excinfo.value)), (
        "no token may remain in the formatted traceback"
    )


def test_publish_run_with_a_cyclic_error_from_the_runner_is_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An injected runner's cyclic, token-bearing error leaves ``run`` sanitized."""
    rig = PublishRig.create(tmp_path)
    cyclic: list[object] = [f"token {_LEAK}"]
    cyclic.append(cyclic)

    def runner(*_args: object, **_kwargs: object) -> tuple[int, str, str]:
        """Fail with an error whose arguments contain a cycle and the token."""
        message = "runner failed"
        raise RuntimeError(message, cyclic)

    caught: list[BaseException] = []
    try:
        rig.run(
            monkeypatch,
            command_runner=runner,
            expect_digests=rig.digests_file(),
            environment=CargoEnvironment(None, None, (_LEAK,)),
        )
    except RuntimeError as exc:
        caught.append(exc)

    assert caught, "the runner's error must reach the caller with its type"
    assert _LEAK not in "".join(traceback.format_exception(caught[0])), (
        "the formatted traceback must be clean"
    )
    assert caught[0].args[1][1] is caught[0].args[1], "the cycle must survive"
