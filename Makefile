MDLINT ?= $(shell which markdownlint)
NIXIE ?= $(shell which nixie)
MDFORMAT_ALL ?= $(shell which mdformat-all)
UV ?= $(shell command -v uv 2>/dev/null || printf '%s/.local/bin/uv' "$$HOME")
# Pin Ruff so `make` invokes the same version as the `ruff==` dev dependency
# in pyproject.toml and the `uv tool install ruff==` step in
# .github/workflows/ci.yml. Bump all three sites together: a version mismatch
# causes version-skew lint failures because rule sets differ between Ruff
# releases.
RUFF_VERSION ?= 0.15.12
RUFF ?= $(UV) tool run --from ruff==$(RUFF_VERSION) ruff
TYPOS_VERSION ?= 1.48.0
# Pin ty so `make typecheck` invokes the same version as the
# `uv tool install ty==` step in .github/workflows/ci.yml. Bump both sites
# together: a version mismatch lets a newer ty flag diagnostics locally that CI
# misses (or vice versa), which is how such a failure slips into CI.
TY_VERSION ?= 0.0.32
TY ?= $(UV) tool run ty@$(TY_VERSION)
UV_ENV = UV_CACHE_DIR=.uv-cache UV_TOOL_DIR=.uv-tools
TOOLS = $(MDFORMAT_ALL) $(MDLINT) $(NIXIE) $(UV)
PY_SOURCES := $(sort $(shell find lading scripts -type f -name '*.py' -print))
VENV_TOOLS = interrogate pytest
PYLINT_PYTHON ?= pypy
PYLINT_TARGETS ?= lading scripts tests
PYLINT_PYPY_SHIM_REF ?= 726d09f968b4d729ee4b29c71fc732e744854f3b
PYLINT_PYPY_SHIM = git+https://github.com/leynos/pylint-pypy-shim.git@$(PYLINT_PYPY_SHIM_REF)
PYLINT = $(UV) tool run --python $(PYLINT_PYTHON) --from '$(PYLINT_PYPY_SHIM)' pylint-pypy

.PHONY: help all clean build build-release lint fmt check-fmt \
	markdownlint nixie spelling spelling-helper-test test typecheck crosshair \
	$(TOOLS) $(VENV_TOOLS)

.DEFAULT_GOAL := all

all: check-fmt lint test typecheck spelling

.venv: pyproject.toml $(UV)
	$(UV) venv --clear

build: $(UV) .venv ## Build virtual-env and install deps
	$(UV) sync --group dev

build-release: build ## Build artefacts (sdist & wheel)
	$(UV) run python -m build --sdist --wheel

clean: ## Remove build artifacts
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

fmt: $(UV) $(MDFORMAT_ALL) ## Format sources
	$(RUFF) format
	$(RUFF) check --select I --fix
	$(MDFORMAT_ALL)

check-fmt: $(UV) ## Verify formatting
	$(RUFF) format --check
	# mdformat-all doesn't currently do checking

lint: build $(UV) interrogate ## Run linters
	$(RUFF) check
	$(UV) run interrogate --fail-under 100 lading
	$(PYLINT) $(PYLINT_TARGETS)

typecheck: build $(UV) ## Run typechecking
	$(UV_ENV) $(TY) check --python-version 3.13 $(PY_SOURCES)

markdownlint: spelling $(MDLINT) ## Lint Markdown files and enforce spelling
	find . -type f -name '*.md' \
	  -not -path './.venv/*' -print0 | xargs -0 $(MDLINT)

spelling: spelling-helper-test ## Enforce en-GB-oxendict spelling in Markdown prose
	@$(UV_ENV) $(UV) run scripts/generate_typos_config.py
	@git ls-files -z '*.md' | \
		xargs -0 -r env $(UV_ENV) $(UV) tool run typos@$(TYPOS_VERSION) \
		--config typos.toml --force-exclude

spelling-helper-test: ## Validate the shared spelling-policy integration
	@$(UV_ENV) $(UV) tool run ruff@$(RUFF_VERSION) format --isolated \
		--target-version py313 --check scripts/generate_typos_config.py \
		scripts/typos_rollout.py scripts/typos_rollout_cache.py \
		scripts/tests/test_typos_rollout.py
	@$(UV_ENV) $(UV) tool run ruff@$(RUFF_VERSION) check --isolated \
		--target-version py313 scripts/generate_typos_config.py \
		scripts/typos_rollout.py scripts/typos_rollout_cache.py \
		scripts/tests/test_typos_rollout.py
	@PYTHONPATH=scripts $(UV_ENV) $(UV) run --no-project --python 3.13 \
		--with pytest==9.0.2 --with pytest-cov==7.0.0 \
		python -m pytest scripts/tests/test_typos_rollout.py \
		-c /dev/null --rootdir=. -p no:cacheprovider \
		--cov=generate_typos_config --cov=typos_rollout \
		--cov=typos_rollout_cache --cov-fail-under=90

nixie: $(NIXIE) ## Validate Mermaid diagrams
	nixie --no-sandbox

test: build $(UV) pytest ## Run tests
	$(UV) run pytest -v

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
