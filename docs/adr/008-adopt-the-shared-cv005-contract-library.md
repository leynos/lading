# ADR-008: Adopt the shared CV-005 contract library

## Status

Accepted.

## Date

2026-10-10

## Context and problem statement

lading keeps CodeScene coverage owned by `main`, the estate rule CV-005, and
held it with local tests (`codescene_environment_rules`, the token and
ownership contracts). Its workflows had also not finished the move: the
pull-request lane generated coverage on a push as well, uploaded the report as
an artefact of its own, ran without a read-only token scope, and the two
coverage actions sat on different commits. The estate has one shared
implementation of the rule, `cv005-contracts` in `leynos/shared-actions`.

## Decision outcome

`make test-workflow-contracts` runs `cv005-contracts check --repository .`
through `uv tool run`, from the full commit named by `CV005_CONTRACTS_REF` in
the `Makefile`; `make test` depends on it and CI runs it in its own step.
`.github/cv005.toml` holds `repository` and `interpreter = "3.14"`, so every
`generate-coverage` call pins `UV_PYTHON` to the repository's Python baseline.

The workflows follow the rule. The lane's `Generate coverage` step runs on a
pull request only, with the ratchet set literally and the artefact declined,
and its job holds `contents: read`. The lane no longer uploads `coverage.xml`:
`coverage-main.yml` owns every upload. The publisher's checkout keeps no
credential. Both coverage actions sit on one commit.

`tests/workflow_contracts/test_cv005_wiring.py` holds the local wiring: it
fails if the pin is not a full commit, if the target stops running the pinned
checker with `check --repository .` under Python 3.14, if the repository
parameter is wrong, if `make all` or `make test` drops the target, or if CI
stops running it without condition. The local contract modules stay for now;
retiring the ones the library holds is a separate change.

## Known risks and limitations

- A fix to the rules reaches this repository only as a pin bump.
- Until the local modules are retired, a clause is checked twice.
- The lane's `coverage-report` artefact is gone; nothing in the repository
  downloaded it.
