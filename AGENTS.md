# Assistant instructions

## Code style and structure

- **Code is for humans.** Write code with clarity and empathy—assume a
  tired teammate will need to debug it at 3 a.m.
- **Comment *why*, not *what*.** Explain assumptions, edge cases, trade-offs,
  or complexity. Don't echo the obvious.
- **Clarity over cleverness.** Be concise, but favour explicit over terse or
  obscure idioms. Prefer code that's easy to follow.
- **Use functions and composition.** Avoid repetition by extracting reusable
  logic. Prefer generators or comprehensions, and declarative code to
  imperative repetition when readable.
- **Small, meaningful functions.** Functions must be small, clear in purpose,
  single responsibility, and obey command/query segregation.
- **Clear commit messages.** Commit messages should be descriptive, explaining
  what was changed and why.
- **Name things precisely.** Use clear, descriptive variable and function names.
  For booleans, prefer names with `is`, `has`, or `should`.
- **Structure logically.** Each file should encapsulate a coherent module. Group
  related code (for example, models + utilities + fixtures) close together.
- **Group by feature, not layer.** Colocate views, logic, fixtures, and helpers
  related to a domain concept rather than splitting by type.
- **Use consistent spelling and grammar.** Comments must use en-GB-oxendict
  ("-ize" / "-yse" / "-our") spelling and grammar, with the exception of
  references to external APIs. See [Spelling](#spelling).
- **Document public APIs comprehensively.** Public functions, classes, and
  methods must have comprehensive NumPy-style docstrings, including clear
  examples that demonstrate usage and outcome where appropriate.
- **Keep private helper docstrings concise.** Prefer single-line docstrings for
  private helpers. When a private helper needs an explanatory paragraph,
  inspect whether that need exposes conflated responsibilities, an unclear
  command/query boundary, or another CQRS or cohesion failure:
  - Split the helper when it performs distinct query and command
    responsibilities or combines unrelated concerns.
  - Extract a focused helper when doing so makes the invariant or boundary local
    and simpler.
  - Keep the helper intact when its responsibility is cohesive and the
    explanation documents an unavoidable local constraint; retain the paragraph
    in that case.
- **Structure private helper docstrings selectively.** Use structured
  NumPy-style sections for private helpers only when they describe non-obvious
  behaviour.
- **Keep test documentation meaningful.** Test documentation should omit
  examples that only restate the test logic.
- **Keep file size manageable.** No single code file may be longer than 400
  lines. Long switch statements or dispatch tables should be broken up by
  feature and constituents colocated with targets. Large blocks of test data
  should be moved to external data files.
- **Use clear file boundaries.** Each module, component, and package should have
  an obvious responsibility and avoid accidental coupling.

## Documentation maintenance

- **Reference:** Use the markdown files within the `docs/` directory as a
  knowledge base and source of truth for project requirements, dependency
  choices, and architectural decisions. Start with
  [documentation contents](docs/contents.md) and
  [repository layout](docs/repository-layout.md) when orienting within the
  project.
- **Update:** When new decisions are made, requirements change, libraries are
  added/removed, or architectural patterns evolve, **proactively update** the
  relevant file(s) in the `docs/` directory to reflect the latest state. Ensure
  the documentation remains accurate and current.
- **Design decisions:** Record design decisions in the relevant design
  document. When a decision is substantive, capture it in an Architectural
  Decision Record (ADR) following the documentation style guide, and reference
  that ADR from the design document.
- **User-facing behaviour:** Update [users' guide](docs/users-guide.md) for
  behaviour or user-interface changes that users should know about. Add
  migration guidance for compatibility or usage changes.
- **Internal interfaces:** Document internally facing interfaces in the
  relevant component architecture document. Document internally facing
  conventions and practices in [developers' guide](docs/developers-guide.md).
- **Build tooling:** When changing build tooling, document the effective
  toolchain, inherited flags, and deliberate differences between validation
  commands.
- **Style:** All documentation must adhere to the
  [documentation style guide](docs/documentation-style-guide.md).
- **Accuracy:** Verify changed examples, commands, imports, links, paths, and
  stated guarantees against the implementation. Update maintained copies and
  indexes when names or locations change. Keep transient validation results in
  the PR or execution record.
- **Decision history:** Preserve accepted ADR decisions; record subsequent
  changes through the
  [ADR amendment and supersession process](docs/documentation-style-guide.md#architectural-decision-records-adrs).
- **Completed execplans:** Completed execplans are historical documents and
  should reflect the state of the repository at time of implementation. Do not
  retroactively update completed execplan documents to reflect subsequent
  changes in API, structures, or behaviour.

## Planning and verification

- For non-trivial work, prepare or update a self-contained ExecPlan using the
  available planning guidance. Identify the outcome, relevant roadmap and
  design references, prerequisites, scope exclusions, and observable acceptance
  criteria. Signpost relevant documentation and skills.
- Identify affected behavioural contracts, invariants, and failure modes before
  choosing the implementation. Use these to pressure-test the proposed API and
  verification strategy.
- Plan the smallest useful increment. Preserve decision gates and stopping
  conditions; defer abstractions, dependencies, macros, or published components
  until the preceding evidence justifies them.
- Give each major milestone explicit acceptance commands, including applicable
  proof checks and the formatting, linting, and test gates. Keep commits
  coherent and useful as rollback points.
- When plan approval is required, deliver the plan for review and wait for
  approval before implementation. Keep the active plan current, and mark
  relevant roadmap entries complete only after their acceptance criteria pass.

## Change quality & committing

- **Atomicity:** Aim for small, focused, atomic changes. Each change (and
  subsequent commit) should represent a single logical unit of work.
- **Quality gates:** Before considering a change complete or proposing a
  commit, ensure all of the following are met:
  - New functionality or behaviour changes are fully validated by relevant unit
    and behavioural tests.
  - Bug fixes include a failing test before the fix and a passing test
    afterwards.
  - Code passes lint checks.
  - Formatting is correct and validated.
- **For Python files:**
  - **Testing:** Passes all relevant unit and behavioural tests (`make test`).
  - **Linting:** Passes the complete `make lint` pipeline: Ruff, Interrogate,
    Pylint, the blocking, strict, production-only Skylos dead-code scan, and the
    blocking code-duplication gate. Skylos runs under Python 3.14 because it
    parses source with its own runtime AST; pinning that interpreter avoids
    phantom findings for newer syntax.
    Investigate every Skylos finding and remove genuine dead code. After
    verifying a false positive,
    prefer a precise, typed entry-point rule in `[tool.skylos.dead_code]` with
    its fully qualified symbol and a reason that names the verified caller.
    Use `type = "method"` for methods. Use
    `make skylos-allow SYMBOL=handler REASON="verified caller"` only when an
    entry-point rule cannot describe the boundary. Both variables must contain
    non-whitespace content; `NAME` is reserved by WSL for the host name and
    must not be used for Skylos symbols. Skylos records the symbol only, so
    retain the caller-specific rationale with the reviewing change.
  - **Code duplication:** Passes the blocking nose duplication gate
    (`make duplication`, run as the final stage of `make lint`). The objective
    is to remove unjustified repeated logic and keep intentional parallels
    explicitly reviewable, **not** to reach a low duplication count through
    indiscriminate abstraction or suppression. Investigate every reported
    family and extract the smallest coherent shared implementation at the
    correct architectural layer when the repetition is genuine. When the
    parallel structure is intentional, record it with `make duplication-allow`
    (see the developer guide for its arguments), naming the specific
    independent contracts or architectural boundary an extraction would wrongly
    couple. Never reduce the ranking
    budget, raise the size floor, or widen an exception merely to obtain a
    green run. Never use a repository-wide wildcard, a mass-generated reason,
    or automatic allowlisting. `make duplication` provisions the pinned
    detector itself. [ADR-010](docs/adrs/adr-0010-adopt-nose-duplication-gate.md)
    records the policy.
  - **Formatting:** Adheres to formatting standards (`make check-fmt`; use
    `make fmt` to apply fixes).
  - **Typechecking:** Passes type checking (`make typecheck`).
- **For Markdown files (`.md` only):**
  - **Linting:** Passes markdown lint checks (`make markdownlint`).
  - **Mermaid diagrams:** Passes validation using nixie (`make nixie`).
- **Before review:** Inspect the complete diff, including tests, fixtures,
  scripts, examples, and documentation. Check file-size limits, module
  documentation, doc-comment placement, assertion diagnostics, duplicated test
  setup, and applicable spelling and formatting gates. Link relevant roadmap
  entries, issues, and the ExecPlan in the PR description.
- **Committing:**
  - Only changes that meet all the quality gates above should be committed.
  - Write clear, descriptive commit messages summarizing the change, following
    these formatting guidelines:
    - **Imperative Mood:** Use the imperative mood in the subject line (e.g.,
      "Fix bug", "Add feature" instead of "Fixed bug", "Added feature").
    - **Subject Line:** The first line should be a concise summary of the change
      (ideally 50 characters or fewer).
    - **Body:** Separate the subject from the body with a blank line. Subsequent
      lines should explain the *what* and *why* of the change in more detail,
      including rationale, goals, and scope. Wrap the body at 72 characters.
    - **Formatting:** Use Markdown for any formatted text (like bullet points or
      code snippets) within the commit message body.
  - Do not commit changes that fail any of the quality gates above.

## Refactoring heuristics & workflow

- **Recognizing refactoring needs:** Regularly assess the codebase for potential
  refactoring opportunities. Perform refactoring when observing:
  - **Long methods/functions:** Functions or methods that are excessively long
    or try to do too many things.
  - **Duplicated code:** Identical or very similar code blocks appearing in
    multiple places.
  - **Complex conditionals:** Deeply nested or overly complex `if`/`else` or
    `switch` statements (high cyclomatic complexity).
  - **Large code blocks for single values:** Significant chunks of logic
    dedicated solely to calculating or deriving a single value.
  - **Primitive obsession / data clumps:** Groups of simple variables (strings,
    numbers, booleans) that are frequently passed around together, often
    indicating a missing class or object structure.
  - **Excessive parameters:** Functions or methods requiring a very long list of
    parameters.
  - **Feature envy:** Methods that seem more interested in the data of another
    class/object than their own.
  - **Shotgun surgery:** A single change requiring small modifications in many
    different classes or functions.
- **Abstraction / port / helper policy:** Before implementing an abstraction,
  port, or extracted helper, the agent must:
  1. Sweep the repository and approved dependencies for equivalent behaviour.
     Reuse it unless demonstrably unsuitable. New abstractions must simplify
     the current change or have a concrete reuse case.
  2. Document the new abstraction's intended scope and re-use policy
     (ownership boundaries, permitted call-sites, and composition rules).
  3. Record the decision in the appropriate architecture, design, or
     developers-guide document using `docs/contents.md` as the index to select
     the correct location.
- **Post-Commit Review:** After committing a functional change or bug fix (that
  meets all quality gates), review the changed code and surrounding areas using
  the heuristics above.
- **Separate Atomic Refactors:** If refactoring is deemed necessary:
  - Perform the refactoring as a **separate, atomic commit** *after* the
    functional change commit.
  - Ensure refactoring adheres to the testing guidelines (behavioural tests
    pass before and after, unit tests added for new units).
  - Ensure the refactoring commit itself passes all quality gates.

## Architecture and operational boundaries

- Keep domain policy separate from filesystem, process, network, framework,
  and telemetry adapters. Inject dependencies where needed through existing
  interfaces or narrow closures; avoid speculative layers and dependency cycles.
- Make fallibility, mutation, and side-effects explicit in APIs. Preserve I/O,
  parsing, and permission errors rather than silently converting them into
  absence, `false`, or empty success. Compose side-effects at orchestration
  boundaries; queries must not hide writes or unrelated workflow policy.
- For streams, subprocesses, retries, caches, queues, and background tasks,
  define applicable size limits, deadlines, ownership, cancellation, and
  cleanup. A timeout does not bound accumulated bytes. Include waiting and
  cleanup in the operation's budget. Define partial-failure and retry behaviour
  where updates span multiple effects; keep locks narrow and ordered, avoiding
  holds across awaits or external calls unless explicitly justified.
- Validate untrusted inputs before privileged effects, use structured APIs
  instead of executable string construction, and grant only required
  permissions.
- When performance materially motivates a change, measure realistic inputs and
  distinguish build, execution, queueing, and cache state. Avoid repeated I/O
  or unnecessary allocation in per-item loops and blocking work on async
  executors.

## Gate commands

This repository is a Python project with no Rust sources. The template's Rust
guidance does not apply here, so it is not imported.

- Run `make check-fmt`, `make lint`, and `make test` before committing. These
  targets wrap the following commands, so contributors understand the exact
  behaviour and policy enforced:
  - `make check-fmt` runs `ruff format --check` over the Python sources and the
    Markdown formatter check, which applies the `mdtablefix` rules `--wrap
    --renumber --breaks --ellipsis --fences`. It changes no files.
  - `make lint` depends on `build`, `interrogate`, `skylos`, and `duplication`.
    It runs `ruff check`, `make lint-docstrings` (interrogate with
    `--fail-under 100` and no `MISSED` rows), Pylint, the df12 Pylint rules, and
    `ambrleaks` over `tests`. The Skylos and duplication stages are described in
    the Change quality & committing section.
  - `make test` depends on `build`, `pytest`, `makeutil`, and
    `test-workflow-contracts`. It runs pytest with `--doctest-modules`, so
    examples in module and function docstrings are executed.
- `make typecheck` runs `ty check` on the Python sources at the 3.14 baseline.
- `make fmt` applies the fixes: `ruff format`, `ruff check --select I --fix`,
  the Markdown formatter in place, and `markdownlint-cli2 --fix` over all
  Markdown files.
- `make markdownlint` runs the en-GB-oxendict spelling gate before
  `markdownlint-cli2` over the tracked Markdown files.
- `make nixie` validates Mermaid diagrams.

## Python verification and testing

- For Python work, use `pytest` for unit tests and `pytest-bdd` for behavioural
  tests. Cover happy paths, unhappy paths, and relevant edge cases.
- Snapshot tests (using `syrupy`) should be provided where multivariant output
  format consistency is relevant to the requirements. Snapshot tests must
  capture meaningful, reviewer-useful contracts rather than generic dumps of
  broad objects. Keep snapshots focused on stable output boundaries, pair them
  with semantic assertions for the behaviours they represent, and avoid
  snapshot-only coverage for logic that can be asserted directly. Redact or
  normalize nondeterministic fields such as timestamps, absolute paths, random
  identifiers, ordering-dependent maps, hostnames, and environment-specific
  values before snapshotting. Do not accept brittle snapshots that churn after
  harmless formatting or dependency changes; narrow the captured output until a
  snapshot failure identifies a real contract change.
- Add end-to-end tests where a change affects externally observable workflows,
  integration contracts, persistence, command-line behaviour, network
  boundaries, user interface flows, or other system-level behaviour.
- Use property tests with `hypothesis` or `CrossHair` when a change introduces
  an invariant over a range of inputs, states, orderings, or transitions.
- Run relevant unit, behavioural, property, and end-to-end suites before and
  after each change.

## Markdown guidance

- Validate Markdown files using `make markdownlint`.
- Run `make fmt` after documentation changes to format all Markdown files and
  fix table markup.
- Validate Mermaid diagrams in Markdown by running `make nixie`.
- Markdown paragraphs and bullet points must be wrapped at 80 columns.
- Code blocks must be wrapped at 120 columns.
- Tables and headings must not be wrapped.
- Use dashes (`-`) for list bullets.
- Use GitHub-flavoured Markdown footnotes (`[^1]`) for references and
  footnotes.
- Avoid first-person prose outside README and BDD feature files; allow
  second-person prose in README, AGENTS, BDD feature files, and agent skill
  directories. These exceptions qualify the documentation style guide.
- Quoted API names and identifiers retain upstream spelling. Mark them as
  code using backticks or fenced blocks rather than adding spelling exceptions.

<!-- typos-config-builder:agents-md:start -->

## Spelling

- `make spelling` runs the pinned `typos-config-builder gate`, which
  regenerates `typos.toml` from the shared en-GB-oxendict dictionary and
  `typos.local.toml`, then checks spelling and the shared phrase corrections.
- `typos.toml` is generated: never edit it by hand. Put narrow
  repository-specific exceptions in `typos.local.toml`, as exact or full-line
  patterns rather than bare accepted words.
- When `make spelling` changes `typos.toml`, commit the regenerated file. If
  the change is unrelated to your work, commit it in a separate base pull
  request and stack your branch on it, so each review diff stays focused.

<!-- typos-config-builder:agents-md:end -->

## Project documentation

Record design decisions in the design document. Where a decision is
substantive, record it in an ADR document following the documentation style
guide, then reference that ADR from the design document.

Update `docs/users-guide.md` for any change to application behaviour or user
interface that users should know about. Document internally facing interfaces
or practices in the relevant component architecture document. Document
internally facing conventions or practices in `docs/developers-guide.md`.

## Python development guidelines

For Python development, refer to the detailed guidelines in the `.rules/`
directory:

- [Python code style guidelines](.rules/python-00.md) - Core Python 3.14 style
  conventions.
- [Python context managers](.rules/python-context-managers.md) - Best practices
  for context managers.
- [Python exceptions and logging][python-exceptions] -
  Raising and handling exceptions and logging.
- [Python generators](.rules/python-generators.md) - Generator and iterator
  patterns.
- [Python project configuration](.rules/python-pyproject.md) -
  `pyproject.toml` and packaging.
- [Python return patterns](.rules/python-return.md) - Function return
  conventions.
- [Python typing](.rules/python-typing.md) - Type annotation best practices.

[python-exceptions]: .rules/python-exception-design-raising-handling-and-logging.md

Additional docs:

- [Scripting standards](docs/scripting-standards.md) - Guidance for writing
  robust scripts, including secure command execution via `cuprum`, catalogue
  allowlisting, and command mocking patterns with `cmd-mox`.
- Before adding or updating helper scripts, read the scripting standards guide
  and follow its `Cyclopts`, `cuprum`, `pathlib`, and `cmd-mox` conventions.
