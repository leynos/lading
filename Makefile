MDLINT ?= $(shell command -v markdownlint-cli2 2>/dev/null || printf '%s' "$$HOME/.bun/bin/markdownlint-cli2")
NIXIE ?= $(shell which nixie)
# `make fmt` and `make check-fmt` call mdtablefix directly. `--git` selects the
# Markdown files Git tracks and `--include-untracked` adds the untracked files
# Git does not ignore, so a new document is formatted before it is staged.
# Both modes need mdtablefix 0.6.0 or later; CI pins the version in
# .github/workflows/ci.yml.
MDTABLEFIX ?= mdtablefix
MDTABLEFIX_SELECT = --git --include-untracked
MDTABLEFIX_RULES = --wrap --renumber --breaks --ellipsis --fences
UV ?= $(shell command -v uv 2>/dev/null || printf '%s/.local/bin/uv' "$$HOME")
# Pin Ruff so `make` invokes the same version as the `ruff==` dev dependency
# in pyproject.toml. Bump both together: a version mismatch causes version-skew
# lint failures because rule sets differ between Ruff releases. The dev
# dependency is what Continuous Integration runs, so this default follows it
# on the `dependabot/uv/ruff-*` bump rather than moving independently. The
# assertions in `tests/workflow_contracts/test_python_lint_gateway.py` hold
# the two spellings equal.
RUFF_VERSION ?= 0.16.10
RUFF ?= $(UV) tool run --from ruff==$(RUFF_VERSION) ruff
TYPOS_CONFIG_BUILDER_VERSION ?= v0.1.3
TYPOS_CONFIG_BUILDER = $(UV) tool run --from \
	"git+https://github.com/leynos/typos-config-builder.git@$(TYPOS_CONFIG_BUILDER_VERSION)" \
	typos-config-builder
# Pin ty so `make` and CI invoke the same typechecker release. ty is
# pre-1.0 and diagnostics shift between releases, so an unpinned install
# breaks the typecheck gate without any code change. Bump deliberately and
# fix new diagnostics in the same commit.
TY_VERSION ?= 0.0.56
TY ?= $(UV) tool run --from ty==$(TY_VERSION) ty
UV_ENV = UV_CACHE_DIR=.uv-cache UV_TOOL_DIR=.uv-tools
TOOLS = $(MDLINT) $(NIXIE) $(UV)
# The single Python baseline every gateway derives from. Ruff's
# `target-version`, Pylint's `py-version`, the managed interpreters behind
# `uv tool run`, and `ty --python-version` all read this value, so bumping the
# baseline is one edit here plus the mirrors that
# tests/workflow_contracts/test_python_baseline_contract.py pins.
PYTHON_BASELINE ?= 3.14
# Every Python source in the repository, the installed package included. A
# module under `lading`, `.github` or `benches` is linted and typechecked the
# moment it lands: the roots are discovered rather than enumerated, so no new
# tree can quietly escape the gate. `lading` is a root in its own right, not
# something reached through another tree -- omitting it leaves the production
# package entirely ungated while the suite still passes. The prune list keeps
# dependency caches and build dropouts out; `-type d` prunes cost one directory
# visit each, where a `-not -path` test would still descend into them.
PYTHON_SOURCE_ROOTS ?= lading .github tests scripts benches benchmarks
PYTHON_PRUNED_DIRECTORIES = \
	.git .venv .uv-cache .uv-tools .hypothesis .pytest_cache .ruff_cache \
	__pycache__ node_modules
PYTHON_PRUNE_TESTS = $(foreach d,$(PYTHON_PRUNED_DIRECTORIES),-name $(d) -prune -o)
PYTHON_EXISTING_SOURCE_ROOTS = $(wildcard $(PYTHON_SOURCE_ROOTS))
# A `*.py` sweep alone is not the whole rule, and this is the one hole the file
# list would otherwise leave. `scripts/publish-check/bin/cargo` is a Python
# source with no extension, because `cargo` is the name that has to sit on
# `PATH` for the shim to shadow the real binary; a suffix test cannot see it, so
# the gate would report green while that file went unchecked. The second
# predicate reads the file's shebang instead, which is precisely what makes it
# Python and survives edits to the body.
#
# The test is deliberately spelled with `awk` rather than a `grep` on a `#!`
# pattern. `#` opens a Make comment even inside quotes and needs escaping, and
# the brace expression a shebang wants collides with `find`'s own `\( \)`. The
# form below carries no `#`, no backslash and no bracket expression across the
# Make-to-shell boundary, so there is nothing left for a quoting layer to eat.
# Spelling the match `uv run python` rather than a bare `python` keeps an
# unrelated shell or Perl script out of a Python lint run. `-o` plus `-print`
# makes this a union: a `.py` file whose shebang matches is still listed once.
#
# Discovery is fail-closed, and that costs three lines rather than one. The
# obvious spelling pipes the output through `sort`, which silently discards
# `find`'s status: a pipeline exits with the status of its *last* command, so a
# `find` that could not read a root at all would be reported as a success by
# the `sort` that ran after it and exited cleanly. Sorting with Make's own
# `$(sort)` keeps `find` the only command in the shell call. `.SHELLSTATUS`
# then has to be read on the very next line, because it reports only the most
# recent `$(shell ...)` and any intervening call would overwrite it.
#
# This is the failure that matters: a list truncated by a failed `find` is not
# empty, so every gate still runs and still reports green over the files that
# survived, while the missing ones go unread by all of them at once.
#
# The test is `$(filter-out 0,...)` rather than an equality against zero so
# that an *empty* status passes too. Make older than 4.2 has no
# `.SHELLSTATUS`, and a guard that turned an older `make` into a hard build
# failure would be a worse bug than the one it closes; the check is an extra
# net beneath the modern `make` the CI image pins, not the only thing keeping
# discovery correct.
#
# The guard is only as good as what `find` reports, and the two `-exec` forms
# differ in exactly that. With the per-file `\;` terminator `find` discards the
# child's status: an unreadable extensionless script makes `awk` exit 2, the
# file is dropped from the list, and `find` still exits zero -- the same status
# a non-match produces, so the guard cannot see the failure it exists for. The
# batch `+` terminator folds a non-zero child status back into `find`'s own.
# The predicate has to change shape with the terminator, because `+` runs the
# utility once per batch rather than once per file and `awk`'s exit result can
# no longer stand in for a per-file test: the `*.py` branch prints the name
# itself, and `awk` prints the name it matched.
#
# That shape carries one trap of its own. A program that exited non-zero on a
# batch with no match would now trip the guard on every run, since a declared
# but empty root such as `benches` has no extensionless match at all, so the
# program prints and falls off the end. The batch's status then reports a read
# or syntax failure and nothing else, which is what the guard is for.
PYTHON_FIND_COMMAND = find $(PYTHON_EXISTING_SOURCE_ROOTS) \
	$(PYTHON_PRUNE_TESTS) -type f \( -name '*.py' -print -o -exec awk \
	'FNR == 1 && /uv run python/ { print FILENAME }' {} + \)
PYTHON_SOURCES_UNSORTED := $(shell $(PYTHON_FIND_COMMAND))
PYTHON_DISCOVERY_STATUS := $(.SHELLSTATUS)
ifneq ($(filter-out 0,$(PYTHON_DISCOVERY_STATUS)),)
  $(error Python source discovery failed: find exited $(PYTHON_DISCOVERY_STATUS). Refusing to gate an incomplete file list; fix the discovery roots or the pruned paths before running any gate)
endif
PYTHON_SOURCES := $(strip $(sort $(PYTHON_SOURCES_UNSORTED)))
# Ruff reads `.` as its default file list, and that sweep is wider than
# `PYTHON_SOURCES` in one direction and narrower in another. It is wider
# because Ruff's own `include` covers `**/pyproject.toml`, which
# `PYTHON_SOURCES` does not list and which would silently lose coverage if the
# variable replaced `.` outright. It is narrower because `.` discovers by file
# extension only, exactly as `find -name '*.py'` does, so the extensionless
# shim above is invisible to it. Passing both spellings is the union: `.` keeps
# the TOML under the gate and the file list adds the shim. The discovery
# predicate and the gate it feeds must agree on what "a Python source" means,
# or the hole this variable exists to close reopens one layer further down.
#
# `--force-exclude` is load-bearing rather than tidy. Ruff applies an
# `exclude` only while it walks a directory; a path named on the command line
# bypasses it. `[tool.ruff.format]` lists `tests/bdd/steps/test_bump_steps.py`
# because that module's deliberate formatting -- a blank line after
# `if TYPE_CHECKING:` and multi-line assertion messages -- is what the file is
# for, so passing the list above would otherwise reformat it and fail
# `check-fmt`. The flag restores the configured exclusions for explicit paths
# while leaving them in force for discovery, which is the behaviour the exclude
# was written against. The lint `exclude` is `extend-exclude` for `.rules` and
# `docs` only, so this does not narrow what `lint` reads. It is a per-subcommand
# option, so it rides alongside each `check`/`format` verb below rather than on
# `RUFF`, which stays the plain pinned invocation the gateway contract test
# matches on.
RUFF_TARGETS = . $(PYTHON_SOURCES)
RUFF_FORCE_EXCLUDE = --force-exclude
# No `--extra-search-path` is passed. Every import in the gated trees is either
# absolute (`lading.…`, `tests.…`) or third-party, so the project root ty
# already uses resolves them all; adding a root such as `lading` to the search
# path turns it into a namespace package instead and breaks the package's own
# relative imports (`from .cli import app` becomes an unresolved module).
PYTHON_TYPE_PATHS ?=
VENV_TOOLS = interrogate pytest
# Pylint runs on managed CPython at the project baseline. The source and the
# df12 rules are both written to that baseline, so the interpreter Pylint parses
# with is the same one the package declares support for.
PYLINT_PYTHON ?= $(PYTHON_BASELINE)
PYLINT_VERSION ?= 4.0.9
PYLINT_TARGETS ?= $(PYTHON_SOURCES)
PYLINT = $(UV) tool run --managed-python --python $(PYLINT_PYTHON) --from 'pylint==$(PYLINT_VERSION)' pylint
# The pin is a commit rather than a tag so a moved tag cannot silently change
# what the gate runs. v0.3.0 registers C9102 (assert-missing-message); the
# gate now reaches it because `PYLINT_TARGETS` is a file list, not a directory.
DF12_PYTHON_LINTS_REF ?= 4cf41736cce2f7ba2778882a5c629c044568a0e5
DF12_PYTHON_LINTS = git+https://github.com/leynos/df12-python-lints.git@$(DF12_PYTHON_LINTS_REF)
DF12_PYTHON ?= $(PYTHON_BASELINE)
# C9112 (redundant-future-annotations) is enabled here with the rest of the
# family. It is not exempted anywhere: at the 3.14 baseline annotations are
# evaluated lazily by default (PEP 649/749), so `from __future__ import
# annotations` no longer changes how this repository's code runs and no module
# keeps it. An annotation that names a `TYPE_CHECKING`-only import must import
# that name at runtime instead.
#
# A pytest-bdd step module looks like it needs the exception and does not.
# pytest-bdd calls `inspect.signature` while collecting, which resolves a step's
# annotations through the *defining module's* globals, so a step annotation
# naming a typing-only import would raise during collection. The step modules
# therefore import those names at runtime, which is a real import and not a
# suppression. The C9112 pass was briefly split out to carry an
# `--ignore-paths` exemption for them, with only one pattern allowed per
# invocation; that split is gone because the exemption had no subject left. The
# rule itself stays enabled, rather than being dropped with the exemption, as
# the only thing that would notice a typing-only import reappearing on a step
# annotation. `pyproject.toml` lists `runtime-evaluated-decorators` for the
# same hazard, so Ruff's `TC004` reports it first.
DF12_PYLINT_MESSAGES = R9101,C9102,R9103,R9104,C9105,C9106,C9107,R9108,R9109,R9110,R9111,C9112
# `--isolated` keeps this pass on a throwaway environment provisioned from the
# pinned ref, so the lint result never depends on the state of `uv.lock`.
DF12_PYLINT_BASE = $(UV_ENV) $(UV) run --isolated --python $(DF12_PYTHON) \
	--with '$(DF12_PYTHON_LINTS)' pylint \
	--disable=all --load-plugins=df12_python_lints \
	--py-version=$(PYTHON_BASELINE)
DF12_PYLINT = $(DF12_PYLINT_BASE) --enable=$(DF12_PYLINT_MESSAGES)
AMBRLEAKS = $(UV_ENV) $(UV) tool run --python $(DF12_PYTHON) \
	--from '$(DF12_PYTHON_LINTS)' ambrleaks
SKYLOS_VERSION ?= 4.33.2
# Skylos parses source using its own Python AST, so an interpreter at the
# project baseline prevents phantom dead-code findings from syntax older tool
# runtimes cannot parse.
SKYLOS_CLI ?= $(UV_ENV) $(UV) tool run --python $(PYTHON_BASELINE) --from 'skylos==$(SKYLOS_VERSION)' skylos
SKYLOS ?= $(SKYLOS_CLI) --config-file pyproject.toml
SKYLOS_PRODUCTION_TARGETS ?= lading
SKYLOS_EXCLUDE_FOLDERS ?= tests
SKYLOS_WHITELIST_LOCK ?= .skylos-whitelist.lock

.PHONY: help all clean build build-release lint fmt check-fmt \
	markdownlint nixie spelling test typecheck crosshair \
	makeutil skylos-allow $(TOOLS) $(VENV_TOOLS)

.DEFAULT_GOAL := all

all: check-fmt lint test typecheck spelling

.venv: pyproject.toml $(UV)
	$(UV) venv --clear

build: $(UV) .venv ## Build virtual-env and install deps
	$(UV) sync --group dev

build-release: build ## Build artefacts (sdist & wheel)
	$(UV) run python -m build --sdist --wheel

clean: ## Remove build artefacts
	rm -rf build dist *.egg-info \
	  .mypy_cache .pytest_cache .coverage coverage.* \
	  lcov.info htmlcov .venv
	find . -type d -name '__pycache__' -print0 | xargs -0 -r rm -rf

define ensure_tool
	@command -v $(1) >/dev/null 2>&1 || { \
	  printf "Error: '%s' is required, but not installed\n" "$(1)" >&2; \
	  exit 1; \
	}
endef

define ensure_tool_venv
	@$(UV) run which $(1) >/dev/null 2>&1 || { \
	  printf "Error: '%s' is required in the virtualenv, but is not installed\n" "$(1)" >&2; \
	  exit 1; \
	}
endef

ifneq ($(strip $(TOOLS)),)
$(TOOLS): ## Verify required CLI tools
	$(call ensure_tool,$@)
endif


ifneq ($(strip $(VENV_TOOLS)),)
.PHONY: $(VENV_TOOLS)
$(VENV_TOOLS): build ## Verify required CLI tools in venv
	$(call ensure_tool_venv,$@)
endif

fmt: $(UV) ## Format sources
	$(RUFF) format $(RUFF_FORCE_EXCLUDE) $(RUFF_TARGETS)
	$(RUFF) check --select I --fix $(RUFF_FORCE_EXCLUDE) $(RUFF_TARGETS)
	$(MDTABLEFIX) --in-place $(MDTABLEFIX_SELECT) $(MDTABLEFIX_RULES)
	@unset FORCE_COLOR; $(MDLINT) --fix "**/*.md"

check-fmt: $(UV) ## Verify formatting
	$(RUFF) format --check $(RUFF_FORCE_EXCLUDE) $(RUFF_TARGETS)
	$(MDTABLEFIX) --check $(MDTABLEFIX_SELECT) $(MDTABLEFIX_RULES)

lint: build $(UV) interrogate ## Run linters
	$(RUFF) check $(RUFF_FORCE_EXCLUDE) $(RUFF_TARGETS)
	$(UV) run interrogate --fail-under 100 lading
	$(UV) run interrogate --fail-under 100 \
		--ignore-nested-functions --ignore-nested-classes tests scripts
	$(PYLINT) $(PYLINT_TARGETS)
	$(DF12_PYLINT) $(PYLINT_TARGETS)
	$(AMBRLEAKS) tests
	$(SKYLOS) $(SKYLOS_PRODUCTION_TARGETS) --exclude $(SKYLOS_EXCLUDE_FOLDERS) --category dead_code --gate \
		--format concise --no-upload --no-provenance --no-grep-verify

skylos-allow: export SKYLOS_SYMBOL = $(value SYMBOL)
skylos-allow: export SKYLOS_REASON = $(value REASON)
skylos-allow: ## Document one named Skylos exception, not an entry point
	@case "$${SKYLOS_SYMBOL}" in *[![:space:]]*) ;; *) printf "Error: SYMBOL is required for a named whitelist exception\\n" >&2; exit 2;; esac
	@case "$${SKYLOS_REASON}" in *[![:space:]]*) ;; *) printf "Error: REASON is required for a named whitelist exception\\n" >&2; exit 2;; esac
	flock "$(SKYLOS_WHITELIST_LOCK)" env $(SKYLOS_CLI) whitelist "$${SKYLOS_SYMBOL}" --reason "$${SKYLOS_REASON}"

typecheck: build $(UV) ## Run typechecking
	$(UV_ENV) $(TY) check --python-version $(PYTHON_BASELINE) \
		$(PYTHON_TYPE_PATHS) $(PYTHON_SOURCES)

markdownlint: spelling $(MDLINT) ## Lint Markdown files and enforce spelling
	git ls-files -z '*.md' | \
		xargs -0 -r $(MDLINT)

spelling: $(UV) ## Enforce en-GB-oxendict spelling
	$(UV_ENV) $(TYPOS_CONFIG_BUILDER) gate --repository .

nixie: $(NIXIE) ## Validate Mermaid diagrams
	nixie --no-sandbox

makeutil: ## Verify the Makefile parser used by contract tests
	$(call ensure_tool,$@)

test: build $(UV) pytest makeutil ## Run tests
	# --doctest-modules collects the examples in module and function
	# docstrings. Without it they are documentation nobody checks: 342 example
	# lines across 45 files were never run before this was added.
	$(UV) run pytest -v --doctest-modules

# Model-check the bump_output pure-helper contracts (issue #95). Only the
# string/count helpers are enumerated: CrossHair 0.0.107 cannot build a symbolic
# proxy for a `pathlib.Path` parameter (it raises in intersect_signatures on
# 3.14, and on the 3.13 it was originally trialled under), so
# `_format_manifest_path` is excluded here and covered instead by the
# Hypothesis property test in tests/unit.
crosshair: build $(UV) ## Model-check bump_output pure-helper contracts (issue #95)
	$(UV) run crosshair check \
	  lading.commands.bump_output._build_changes_description \
	  lading.commands.bump_output._format_header

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | \
	awk 'BEGIN {FS=":"; printf "Available targets:\n"} {printf "  %-20s %s\n", $$1, $$2}'
