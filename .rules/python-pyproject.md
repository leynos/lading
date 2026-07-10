# 1. Overview of `uv` and `pyproject.toml`

Astral's `uv` is a Rust-based project and package manager that uses
`pyproject.toml` as its central configuration file. When you run commands like
`uv init`, `uv sync` or `uv run`, `uv` will:

1. Look for a `pyproject.toml` in the project root and keep a lockfile
   (`uv.lock`) in sync with it.
2. Create a virtual environment (`.venv`) if one does not already exist.
3. Read dependency specifications (and any build-system directives) to install
   or update packages accordingly. [^astral-guides] [^ridgerun]

In other words, your `pyproject.toml` drives everything—from metadata to
dependencies to build instructions—without needing `requirements.txt` or a
separate `setup.py` file. [^levelup] [^pypa-guide]

______________________________________________________________________

## 2. The `[project]` Table (PEP 621)

The `[project]` table is defined by PEP 621 and is now the canonical place to
declare metadata (name, version, authors, etc.) and runtime dependencies. At
minimum, PEP 621 requires:

- `name`
- `version`

However, you almost always want to include at least the following additional
fields for clarity and compatibility:

```toml
[project]
name = "my_project"            # Project name (PEP 621 requirement)
version = "0.1.0"              # Initial semantic version
description = "A brief overview"       # Short summary
readme = "README.md"           # Path to your README file (automatically included)
requires-python = ">=3.10"     # Restrict Python versions, if needed
license = { text = "MIT" }     # SPDX-compatible license expression or file
authors = [
  { name = "Alice Example", email = "alice@example.org" }
]
keywords = ["uv", "astral", "example"]   # (Optional) for metadata registries
classifiers = [
  "Programming Language :: Python :: 3",
  "License :: OSI Approved :: MIT License",
  "Operating System :: OS Independent"
]
dependencies = [
  "requests>=2.25",            # Runtime dependency
  "numpy>=1.23"
]
```

- **`name` and `version`:** Mandatory per PEP 621. [^pypa-guide] [^reddit-uv]
- **`description` and `readme`:** Although not mandatory, they help with
  indexing and packaging tools; `readme = "README.md"` tells `uv` (and PyPI) to
  include your README as the long description. [^astral-guides] [^pypa-guide]
- **`requires-python`:** Constrains which Python interpreters your package
  supports (e.g. `>=3.10`). [^pypa-guide] [^reddit-uv]
- **`license = { text = "MIT" }`:** You can specify a license either as a SPDX
  identifier (via `license = { text = "MIT" }`) or by pointing to a file (e.g.
  `license = { file = "LICENSE" }`). [^pypa-guide] [^reddit-uv]
- **`authors`:** A list of tables with `name` and `email`. Many registries
  (e.g., PyPI) pull this for display. [^pypa-guide] [^reddit-uv]
- **`keywords` and `classifiers`:** These help search engines and package
  indexes. Classifiers must follow the exact trove list defined by PyPA.
  [^pypa-guide] [^reddit-uv]
- **`dependencies`:** A list of PEP 508-style requirements (e.g.,
  `"requests>=2.25"`). `uv sync` will install exactly those versions, updating
  the lockfile as needed. [^astral-guides] [^ridgerun]

______________________________________________________________________

## 3. Optional and Development Dependencies

Modern projects typically distinguish between "production" dependencies (those
needed at runtime) and "development" dependencies (linters, test frameworks,
etc.). In PEP 621, you use `[project.optional-dependencies]` for this:

```toml
[project.optional-dependencies]
dev = [
  "pytest>=7.0",        # Testing framework
  "black",              # Code formatter
  "flake8>=4.0"         # Linter
]
docs = [
  "sphinx>=5.0",        # Documentation builder
  "sphinx-rtd-theme"
]
```

- **`[project.optional-dependencies]`:** Each table key (e.g. `dev`, `docs`)
  defines a "dependency group." You can install a group via
  `uv add --group dev` or `uv sync --include dev`. [^pypa-guide] [^devsjc]
- **Why use groups?** You keep the lockfile deterministic (via `uv.lock`) while
  still separating concerns (test‐only vs. production). [^medium-uv] [^devsjc]

______________________________________________________________________

## 4. Entry Points and Scripts

If you want to expose command-line interfaces (CLIs) or GUIs through your
package, PEP 621 provides the `[project.scripts]` and `[project.gui-scripts]`
tables:

```toml
[project.scripts]
mycli = "my_project.cli:main"    

[project.gui-scripts]
mygui = "my_project.gui:start"
```

- **`[project.scripts]`:** Defines console scripts. When you run `uv run mycli`,
  `uv` will invoke the `main` function in `my_project/cli.py`. [^astral-config]
- **`[project.gui-scripts]`:** On Windows, `uv` will wrap these in a GUI
  executable; on Unix-like systems, they behave like normal console scripts.
  [^astral-config]
- **Plugin Entry Points:** If your project supports plugins, use
  `[project.entry-points.'group.name']` to register them. [^astral-config]

______________________________________________________________________

## 5. Declaring a Build System

PEP 517/518 require a `[build-system]` table to tell tools how to build and
install your project. A "modern" convention is to specify `setuptools>=61.0`
(for editable installs without `setup.py`) or a lighter alternative like
`flit_core`. Below is the typical setup using setuptools:

```toml
[build-system]
requires = ["setuptools>=61.0", "wheel"]
build-backend = "setuptools.build_meta"
```

- **`requires`:** A list of packages needed at build time. For editable installs
  in `uv`, you need at least `setuptools>=61.0` and `wheel`. [^pypa-guide]
  [^astral-config]
- **`build-backend`:** The entry point for your build backend.
  `setuptools.build_meta` is the PEP 517-compliant backend for setuptools.
  [^pypa-guide] [^astral-config]
- **Note:** If you omit `[build-system]`, `uv` will assume
  `setuptools.build_meta:__legacy__` and still install dependencies, but it
  won't editably install your own project unless you set
  `tool.uv.package = true` (see next section). [^astral-config]

______________________________________________________________________

## 6. `uv`-Specific Configuration (`[tool.uv]`)

Astral `uv` allows you to inject its own settings in `[tool.uv]`. The most
common option is:

```toml
[tool.uv]
package = true
```

- **`tool.uv.package = true`:** Forces `uv` to build and install your project
  into its virtual environment every time you run `uv sync` or `uv run`.
  Without this, `uv` only installs dependencies (not your own package) if
  `[build-system]` is missing. [^astral-config]
- You may also set other `uv`-specific keys (e.g., custom indexes, resolver
  policies) under `[tool.uv]`, but `package` is the most common. [^pypa-guide]
  [^astral-config]

______________________________________________________________________

## 7. Putting It All Together: Example `pyproject.toml`

Below is a complete example that demonstrates all sections. Adjust values as
needed for your own project.

```toml
[project]
name = "my_project"
version = "0.1.0"
description = "An illustrative example for Astral uv"
readme = "README.md"
requires-python = ">=3.10"
license = { text = "MIT" }
authors = [
  { name = "Alice Example", email = "alice@example.org" }
]
keywords = ["astral", "uv", "pyproject", "example"]
classifiers = [
  "Programming Language :: Python :: 3",
  "License :: OSI Approved :: MIT License",
  "Operating System :: OS Independent"
]
dependencies = [
  "requests>=2.25",
  "numpy>=1.23"
]

[project.optional-dependencies]
dev = [
  "pytest>=7.0",
  "black",
  "flake8>=4.0"
]
docs = [
  "sphinx>=5.0",
  "sphinx-rtd-theme"
]

[project.scripts]
mycli = "my_project.cli:main"

[build-system]
requires = ["setuptools>=61.0", "wheel"]
build-backend = "setuptools.build_meta"

[tool.uv]
package = true
```

**Explanation of key points:**

1. **Metadata under `[project]`:**

   - `name`, `version` (mandatory per PEP 621) [^pypa-guide]
     [^reddit-uv]
   - `description`, `readme`, `requires-python`: provide clarity about the
     project and help tools like PyPI. [^pypa-guide] [^reddit-uv]
   - `license`, `authors`, `keywords`, `classifiers`: standardized metadata,
     which improves discoverability. [^pypa-guide] [^reddit-uv]
   - `dependencies`: runtime requirements, expressed in PEP 508 syntax.
     [^astral-guides] [^ridgerun]

2. **Optional Dependencies (`[project.optional-dependencies]`):**

   - Grouped as `dev` (for testing + linting) and `docs` (for documentation).
     Installing them is as simple as `uv add --group dev` or
     `uv sync --include dev`. [^pypa-guide] [^devsjc]

3. **Entry Points (`[project.scripts]`):**

   - Defines a console command `mycli` that maps to `my_project/cli.py:main`.
     Invoking `uv run mycli` will run the `main()` function. [^astral-config]

4. **Build System:**

   - `setuptools>=61.0` plus `wheel` ensures both legacy and editable installs
     work. ✱ Newer versions of setuptools support PEP 660 editable installs
     without a `setup.py` stub. [^pypa-guide] [^astral-config]
   - `build-backend = "setuptools.build_meta"` tells `uv` how to compile your
     package. [^pypa-guide] [^astral-config]

5. **`[tool.uv]`:**

   - `package = true` ensures that `uv sync` will build and install your own
     project (in editable mode) every time dependencies change. Otherwise, `uv`
     treats your project as a collection of scripts only (no package).
     [^astral-config]

______________________________________________________________________

## 8. Additional Tips & Best Practices

1. **Keep `pyproject.toml` Human-Readable:** Edit it by hand when possible.
   Modern editors (VS Code, PyCharm) offer TOML syntax highlighting and PEP 621
   autocompletion. [^pypa-guide]

2. **Lockfile Discipline:** After modifying `dependencies` or any `[project]`
   fields, always run `uv sync` (or `uv lock`) to update `uv.lock`. This
   guarantees reproducible environments. [^astral-guides]

3. **Semantic Versioning:** Follow [semver](https://semver.org/) for `version`
   values (e.g., `1.2.3`). Bump patch versions for bug fixes, minor for
   backward-compatible changes, and major for breaking changes. [^pypa-guide]

4. **Keep Build Constraints Minimal:** If you don't need editable installs, you
   can omit `[build-system]` (but then `uv` won't build your package; it will
   only install dependencies). To override, set `tool.uv.package = true`.
   [^astral-config]

5. **Use Exact or Bounded Ranges for Dependencies:** Rather than `requests`, use
   `requests>=2.25, <3.0` to avoid unexpected major bumps. [^devsjc]

6. **Consider Dynamic Fields Sparingly:** You can declare fields like
   `dynamic = ["version"]` if your version is computed at build time (e.g. via
   `setuptools_scm`). If you do so, ensure your build backend supports dynamic
   metadata. [^pypa-guide]

______________________________________________________________________

## 9. Summary

A "modern" `pyproject.toml` for an Astral `uv` project should:

- Use the PEP 621 `[project]` table for metadata and `dependencies`.
- Distinguish optional dependencies under `[project.optional-dependencies]`.
- Define any CLI or GUI entry points under `[project.scripts]` or
  `[project.gui-scripts]`.
- Declare a PEP 517 `[build-system]` (e.g. `setuptools>=61.0`, `wheel`,
  `setuptools.build_meta`) to support editable installs, or omit it and rely on
  `tool.uv.package = true`.
- Include a `[tool.uv]` section, at minimum `package = true` if you want `uv` to
  build and install your own package.

Following these conventions ensures that your project is fully PEP-compliant,
easy to maintain, and integrates seamlessly with Astral `uv`.

[^astral-guides]: <https://docs.astral.sh/uv/guides/projects/>
[^ridgerun]: <https://www.ridgerun.ai/post/uv-tutorial-a-fast-python-package-and-project-manager>
[^levelup]: <https://levelup.gitconnected.com/modern-python-development-with-pyproject-toml-and-uv-405dfb8b6ec8>
[^pypa-guide]: <https://packaging.python.org/en/latest/guides/writing-pyproject-toml/>
[^reddit-uv]: <https://www.reddit.com/r/Python/comments/1ixryec/anyone_used_uv_package_manager_in_production/>
[^devsjc]: <https://devsjc.github.io/blog/20240627-the-complete-guide-to-pyproject-toml/>
[^medium-uv]: <https://medium.com/%40gnetkov/start-using-uv-python-package-manager-for-better-dependency-management-183e7e428760>
[^astral-config]: <https://docs.astral.sh/uv/concepts/projects/config/>
