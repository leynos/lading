# Python 3.14 Code Style Guidelines (with Ruff, ty, and pytest)

## Naming Conventions

- **Directories:** Use *snake\_case* for top-level features or modules (e.g.,
  `data_pipeline`, `user_auth`).
- **Files:** Use *snake\_case.py*; name for contents (e.g., `http_client.py`,
  `task_queue.py`).
- **Classes:** Use *PascalCase*.
- **Variables & Functions:** Use *snake\_case*.
- **Constants:** Use *UPPER\_SNAKE\_CASE* for module-level constants.
- **Private/Internal:** Prefix with a single underscore (`_`) for non-exported
  helpers or internal APIs.

### Python Typing Practices

- **Use typing everywhere.** Enable and maintain full static type coverage. Use
  Pyright for type-checking.
- **Use `TypedDict` or `dataclass` for structured data where appropriate.** For
  internal-only usage, prefer `@dataclass(slots=True)`.
- **Avoid `Any`.** Prefer precise types (`TypeVar`, `Protocol`, `Literal`,
  `Union`) and use `typing.cast[...]` only when necessary—with a justification.
  Use `object` for unknown-but-opaque values.
- **Be explicit with returns.** Use `-> None`, `-> str`, etc., for all public
  functions and class methods.
- **Favour immutability.** Prefer tuples to lists, and `types.MappingProxyType`
  for read-only mappings. If using a third-party `frozendict`, document the
  dependency.

### Tooling and Runtime Practices

- **Enable Ruff.** Use Ruff to lint for performance, security, consistency, and
  style issues. Enable fixers and formatters.
- Use `pyproject.toml` to configure tools like Ruff, Pyright, and Pytest.
- **Enforce `strict` in Pyright.** Treat all Pyright warnings as CI errors. Use
  `# pyright: ignore` sparingly and with explanation.
- **Avoid side effects at import time.** Modules should not modify global state
  or perform actions on import.
- **Treat `.env` as local-only.** Do not commit `.env` files. Load them in
  development (e.g. via `python-dotenv`), and use CI/hosted secret stores in
  pipelines and production.

### Linting and Formatting

- **Use Ruff for linting** (replacing flake8, isort, pyflakes, etc.).
- **Use Ruff for formatting**. Let Ruff handle whitespace and formatting
  entirely—don't fight it.

### Documentation

- **Use docstrings.** Document public functions, classes, and modules using
  NumPy format. For example:

```python
def scale(values: list[float], factor: float) -> list[float]:
    """
    Scale a list of numbers by a given factor.

    Parameters
    ----------
    values : list of float
        The list of numeric values to scale.
    factor : float
        The multiplier to apply to each value.

    Returns
    -------
    list of float
        The scaled numeric values.
    """
    return [v * factor for v in values]
```

- **Explain tricky code.** Use inline comments for non-obvious logic or
  decisions.
- **Colocate documentation.** Keep README.md or `docs/` near reusable packages;
  include usage examples.

### Testing with pytest

- **Colocate unit tests with code** using a unittests subdirectory and a
  `test_` prefix. This keeps logic and its tests together:

  ```text
  user_auth/
    models.py
    login_flow.py
    unittests/
      test_models.py
      test_login_flow.py
  ```

- **Structure integration tests separately.** When tests span multiple
  components, place them under `tests/integration/`:

  ```text
  tests/
    integration/
      test_login_flow.py
      test_user_onboarding.py
  ```

- **Use `pytest` idioms.** Prefer fixtures to setup/teardown methods.
  Parametrize broadly. Avoid unnecessary mocks.

- **Group related tests** using `class` with method names prefixed by `test_`.

- **Write tests from a user's perspective.** Test public behaviour, not
  internals.

- **Avoid mocking too much.** Prefer test doubles only for external services or
  non-deterministic behaviours.

### Example

#### login_flow.py

```python
def login_user(username: str, password: str) -> bool:
    """Return True if the user is authenticated."""
    ...
```

#### login_flow_test.py

```python
def test_login_success():
    assert login_user("alice", "correct-password") is True

def test_login_failure():
    assert not login_user("alice", "wrong-password")
```

______________________________________________________________________

This style guide aims to foster clean, consistent, and maintainable Python 3.14
code with modern tooling. The priority is correctness, clarity, and developer
empathy.
