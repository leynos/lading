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
# in pyproject.toml and the `uv tool install ruff==` step in
# .github/workflows/ci.yml. Bump all three sites together: a version mismatch
# causes version-skew lint failures because rule sets differ between Ruff
# releases.
RUFF_VERSION ?= 0.16.0
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
PY_SOURCES := $(sort $(shell find lading scripts -type f -name '*.py' -print))
VENV_TOOLS = interrogate pytest
PYLINT_PYTHON ?= pypy@3.12
PYLINT_VERSION ?= 4.0.9
PYLINT_TARGETS ?= lading scripts tests
PYLINT = $(UV) tool run --managed-python --python $(PYLINT_PYTHON) --from 'pylint==$(PYLINT_VERSION)' pylint
DF12_PYTHON_LINTS_REF ?= v0.1.0
DF12_PYTHON_LINTS = git+https://github.com/leynos/df12-python-lints.git@$(DF12_PYTHON_LINTS_REF)
DF12_PYTHON ?= 3.14
DF12_PYLINT_MESSAGES = R9101,C9102,R9103,R9104,C9105,C9106,C9107,R9108,R9109,R9110,R9111,C9112
DF12_PYLINT = $(UV_ENV) $(UV) run --isolated --python $(DF12_PYTHON) --with '$(DF12_PYTHON_LINTS)' pylint \
	--disable=all --load-plugins=df12_python_lints \
	--py-version=3.13 --enable=$(DF12_PYLINT_MESSAGES)
AMBRLEAKS = $(UV_ENV) $(UV) tool run --python $(DF12_PYTHON) \
	--from '$(DF12_PYTHON_LINTS)' ambrleaks
SKYLOS_VERSION ?= 4.33.2
# Skylos parses source using its own Python AST, so Python 3.14 prevents
# phantom dead-code findings from syntax older tool runtimes cannot parse.
SKYLOS_CLI ?= $(UV_ENV) $(UV) tool run --python 3.14 --from 'skylos==$(SKYLOS_VERSION)' skylos
SKYLOS ?= $(SKYLOS_CLI) --config-file pyproject.toml
SKYLOS_PRODUCTION_TARGETS ?= lading
SKYLOS_EXCLUDE_FOLDERS ?= tests
SKYLOS_WHITELIST_LOCK ?= .skylos-whitelist.lock
# Pin the nose duplication detector so `make` and CI install the same release.
# Bump the three sites together: this variable, the NOSE_VERSION environment in
# .github/workflows/ci.yml, and [tool.nose].version in pyproject.toml.
# tests/workflow_contracts/test_duplication_toolchain_contract.py fails on drift.
NOSE_VERSION ?= 0.20.0
NOSE_TOOLS_DIR ?= .tools/nose
NOSE_BIN ?= $(NOSE_TOOLS_DIR)/nose
CARGO_BINSTALL ?= cargo-binstall
# The gate wrapper is PEP 723 tooling: `uv run` reads its inline metadata and
# resolves cyclopts/tomlkit on its own interpreter, so the gate never imports
# lading or touches the project virtualenv.
DUPLICATION_GATE = $(UV_ENV) NOSE_BIN=$(NOSE_BIN) $(UV) run scripts/duplication_gate.py

.PHONY: help all clean build build-release lint fmt check-fmt \
	markdownlint nixie spelling test typecheck crosshair \
	makeutil skylos-allow install-nose duplication duplication-test \
	duplication-allow $(TOOLS) $(VENV_TOOLS)

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
	$(RUFF) format
	$(RUFF) check --select I --fix
	$(MDTABLEFIX) --in-place $(MDTABLEFIX_SELECT) $(MDTABLEFIX_RULES)
	@unset FORCE_COLOR; $(MDLINT) --fix "**/*.md"

check-fmt: $(UV) ## Verify formatting
	$(RUFF) format --check
	$(MDTABLEFIX) --check $(MDTABLEFIX_SELECT) $(MDTABLEFIX_RULES)

lint: build $(UV) interrogate install-nose ## Run linters
	$(RUFF) check
	$(UV) run interrogate --fail-under 100 lading
	$(UV) run interrogate --fail-under 100 \
		--ignore-nested-functions --ignore-nested-classes tests scripts
	$(PYLINT) $(PYLINT_TARGETS)
	$(DF12_PYLINT) $(PYLINT_TARGETS)
	$(AMBRLEAKS) tests
	$(SKYLOS) $(SKYLOS_PRODUCTION_TARGETS) --exclude $(SKYLOS_EXCLUDE_FOLDERS) --category dead_code --gate \
		--format concise --no-upload --no-provenance --no-grep-verify
	$(DUPLICATION_GATE) check

# Accept FIRST/SECOND/REASON only from the make command line. Ambient values
# must not satisfy the duplication targets: `FIRST` and `REASON` are common
# enough names that an exported environment could otherwise record an exception
# nobody asked for. `cli_value` therefore reads a variable only when the
# command line set it. Values travel to the recipes through `export`, so a
# reason containing quotes, `$`, or `$(...)` is passed through literally rather
# than re-expanded as Make or shell text.
cli_value = $(if $(filter command line,$(origin $(1))),$(value $(1)))

skylos-allow: export SKYLOS_SYMBOL = $(value SYMBOL)
skylos-allow: export SKYLOS_REASON = $(value REASON)
skylos-allow: ## Document one named Skylos exception, not an entry point
	@case "$${SKYLOS_SYMBOL}" in *[![:space:]]*) ;; *) printf "Error: SYMBOL is required for a named whitelist exception\\n" >&2; exit 2;; esac
	@case "$${SKYLOS_REASON}" in *[![:space:]]*) ;; *) printf "Error: REASON is required for a named whitelist exception\\n" >&2; exit 2;; esac
	flock "$(SKYLOS_WHITELIST_LOCK)" env $(SKYLOS_CLI) whitelist "$${SKYLOS_SYMBOL}" --reason "$${SKYLOS_REASON}"

install-nose: ## Install the pinned nose duplication detector
	@if [ "$$($(NOSE_BIN) --version 2>/dev/null)" = "nose $(NOSE_VERSION)" ]; then \
	  printf "nose %s already installed at %s\\n" "$(NOSE_VERSION)" "$(NOSE_BIN)"; \
	else \
	  printf "Installing nose %s into %s\\n" "$(NOSE_VERSION)" "$(NOSE_TOOLS_DIR)"; \
	  mkdir -p "$(NOSE_TOOLS_DIR)"; \
	  $(CARGO_BINSTALL) --no-confirm --install-path "$(NOSE_TOOLS_DIR)" \
	    --disable-strategies compile,quick-install \
	    --git https://github.com/corca-ai/nose 'nose-cli@$(NOSE_VERSION)'; \
	fi

duplication: install-nose ## Run the blocking code-duplication gate
	$(DUPLICATION_GATE) check

# The gate wrapper and its helper tests run on their own tooling environment,
# not the project virtualenv: `uv run --no-project` ignores pyproject.toml and
# resolves the pinned dependencies from the inline script metadata. The
# application suite must not collect these tests, and they run here instead.
duplication-test: ## Run the duplication-gate helper tests
	@$(UV_ENV) NOSE_BIN=$(NOSE_BIN) $(UV) run --no-project \
		--with pytest==9.1.1 --with cyclopts==4.25.2 \
		--with tomlkit==0.15.1 --with syrupy==5.5.3 \
		--with 'hypothesis[asyncio]==6.163.0' \
		python -m pytest -c /dev/null --rootdir=. -p no:cacheprovider scripts/tests/

duplication-allow: export DUPLICATION_FIRST = $(call cli_value,FIRST)
duplication-allow: export DUPLICATION_SECOND = $(call cli_value,SECOND)
duplication-allow: export DUPLICATION_REASON = $(call cli_value,REASON)
duplication-allow: ## Record one reasoned duplication exception
	@test -n "$${DUPLICATION_FIRST}" || { printf "Error: FIRST is required (path[::name])\\n" >&2; exit 2; }
	@test -n "$${DUPLICATION_REASON}" || { printf "Error: REASON is required for a duplication exception\\n" >&2; exit 2; }
	$(DUPLICATION_GATE) allow --first "$${DUPLICATION_FIRST}" \
		$(if $(call cli_value,SECOND),--second "$${DUPLICATION_SECOND}",) \
		--reason "$${DUPLICATION_REASON}"

typecheck: build $(UV) ## Run typechecking
	$(UV_ENV) $(TY) check --python-version 3.13 $(PY_SOURCES)

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
# both 3.13 and 3.14), so `_format_manifest_path` is excluded here and covered
# instead by the Hypothesis property test in tests/unit.
crosshair: build $(UV) ## Model-check bump_output pure-helper contracts (issue #95)
	$(UV) run crosshair check \
	  lading.commands.bump_output._build_changes_description \
	  lading.commands.bump_output._format_header

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | \
	awk 'BEGIN {FS=":"; printf "Available targets:\n"} {printf "  %-20s %s\n", $$1, $$2}'
