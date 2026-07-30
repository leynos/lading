# flake8-return Style Guide (Python 3.13)

The `flake8-return` rules ensure consistent and explicit return behaviour. They
help keep functions clear in intent and free from unnecessary control flow.
Follow these rules:

## R501 — Avoid Explicit `return None` if It's the Only Return

```python
# BAD:
def func():
    return None

# GOOD:
def func():
    return
```

Use `return` alone instead of `return None` when the function's only result is
`None`.

______________________________________________________________________

## R502 — Avoid Implicit `None` in Functions That May Return a Value

```python
# BAD:
def func(x):
    if x > 0:
        return x
    # implicitly returns None (bad)

# GOOD:
def func(x):
    if x > 0:
        return x
    return
```

Ensure all branches return explicitly if any branch returns a value. Use a bare
`return` when the other result is `None`, as required by R501.

______________________________________________________________________

## R503 — Add a Terminal Return When Another Path Returns a Value

```python
# BAD:
def func(x):
    if x > 0:
        return x
    # no return (bad)

# GOOD:
def func(x):
    if x > 0:
        return x
    return
```

Don't rely on implicit `None` if the function may return a value
elsewhere—always return something at the end. This requirement does not apply
to functions whose only possible result is `None`; they do not need a
redundant bare `return` at the end.

______________________________________________________________________

## R504 — Avoid Redundant Variable Assignment Before `return`

```python
# BAD:
def func():
    result = compute()
    return result

# GOOD:
def func():
    return compute()
```

Inline return expressions unless the variable is reused meaningfully before
returning.

______________________________________________________________________

## R505–R508 — Eliminate Unnecessary `else` After Terminal Statements

Avoid `else` after `return`, `raise`, `break`, or `continue`. These statements
already exit control flow.

```python
# BAD:
if cond:
    return x
else:
    return y

# GOOD:
if cond:
    return x
return y
```

This applies similarly for `raise`, `break`, and `continue`.

```python
# BAD:
for x in xs:
    if x > 0:
        break
    else:
        log()

# GOOD:
for x in xs:
    if x > 0:
        break
    log()
```

These rules apply to regular and `async def` functions alike.

______________________________________________________________________

Use the `flake8-return` rules to enforce predictable and clean return logic,
enhancing readability and correctness.
