# Debugging plan: Windows subprocess relay encoding failure

**Generated**: 2026-09-05 00:07:43 UTC **Issue ID**: Unfiled Windows
`Publish dry run` failure **Severity**: High **Falsification sub-agent**:
`alchemist` **Planning agent boundary**: This document was prepared by the
planning agent. Falsification must be executed by the named sub-agent, not by
the planning agent.

## Problem statement

On Windows GitHub Actions, `lading publish` relays Cargo metadata containing
Polish characters such as `ś` and `ń`. The subprocess output is expected to be
captured completely and mirrored without terminating a relay thread. The
observed traceback reports `UnicodeEncodeError` from
`lading.runtime.subprocess_runner.write_to_sink` after UTF-8 decoding and while
writing to a CP1252 parent text stream.

## Context summary

| Aspect              | Details                                                                                                       |
| ------------------- | ------------------------------------------------------------------------------------------------------------- |
| First observed      | Supplied `rstest-bdd` Windows `Publish dry run` failure                                                       |
| Reproduction rate   | Expected whenever mirrored text contains characters unavailable in the parent stream encoding                 |
| Affected components | Lading subprocess output mirroring and callers using the production `CommandRunner`                           |
| Recent changes      | Reportedly coincident with feature-file rebuild work, but the supplied traceback terminates in the relay path |

_Table 1: Available investigation context._

### Error artefacts

```plaintext
UnicodeEncodeError: 'charmap' codec can't encode character ...

lading/runtime/subprocess_runner.py::relay_stream
lading/runtime/subprocess_runner.py::write_to_sink
```

### Information gaps

- Windows execution is unavailable locally, so the parent stream must be
  represented by a deterministic CP1252-like test double.

______________________________________________________________________

## Hypotheses

### H1: Lading's text mirroring terminates the relay

**Claim**: `write_to_sink` allows `UnicodeEncodeError` from a non-UTF-8 parent
text sink to escape, which terminates `relay_stream` after capture has begun.

**Plausibility**: High — the live implementation catches only
`BrokenPipeError`, and the supplied traceback names both functions.

**Prediction**: If this hypothesis holds, a CP1252-like sink will make
`relay_stream` raise `UnicodeEncodeError`, while the capture list already
contains the decoded Unicode payload.

#### H1 falsification plan

| Step | Action                                                                                 | Expected negative result                                                                                          |
| ---- | -------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| 1    | Relay UTF-8 bytes containing `ś` and `ń` into a sink whose `write()` encodes as CP1252 | No exception, or an exception before the decoded payload reaches the capture list, disproves the claimed boundary |
| 2    | Call `write_to_sink` directly with the same sink and payload                           | No `UnicodeEncodeError` disproves `write_to_sink` as the escaping source                                          |

_Table 2: H1 falsification steps._

**Tooling**: Focused Python invocation using `io.BytesIO` and an in-memory sink.

**Confidence on falsification**: High; the experiment isolates the exact two
functions named in the traceback.

______________________________________________________________________

### H2: Cuprum owns the failing output write

**Claim**: The failing Cargo metadata command reaches the parent text stream
through Cuprum rather than Lading's production subprocess adapter.

**Plausibility**: Low — the live Lading call graph routes workspace metadata
through `lading.runtime.subprocess_runner`, while Cuprum imports are confined
to command catalogue construction.

**Prediction**: If this hypothesis holds, the metadata execution call graph or
the minimal reproduction will enter Cuprum before the failing stream write.

#### H2 falsification plan

| Step | Action                                                                                      | Expected negative result                                                                          |
| ---- | ------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| 1    | Trace the active metadata `CommandRunner` to the concrete stream writer                     | A direct path to Lading's `invoke_via_subprocess` with no Cuprum frame disproves Cuprum ownership |
| 2    | Reproduce the failure by importing only Lading's subprocess module and the standard library | Reproduction without importing Cuprum disproves Cuprum as a necessary cause                       |

_Table 3: H2 falsification steps._

**Tooling**: Leta and CodeGraph call graphs, Python module inspection, and a
focused Python invocation.

**Confidence on falsification**: High; lack of a runtime dependency in the
failing path and reproduction without Cuprum rule out causal ownership.

______________________________________________________________________

### H3: Cargo emits invalid UTF-8 metadata

**Claim**: Invalid Cargo output causes decoder corruption before the parent
stream write.

**Plausibility**: Low — the reported characters have valid UTF-8 encodings, and
the traceback reports encoding to CP1252 rather than decoding from UTF-8.

**Prediction**: If this hypothesis holds, strict UTF-8 decoding of the supplied
characters will fail or produce replacement characters before mirroring.

#### H3 falsification plan

| Step | Action                                                                              | Expected negative result                                                               |
| ---- | ----------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| 1    | Encode `ś` and `ń` as UTF-8 and pass the bytes through Lading's incremental decoder | Exact decoded text with no replacement characters disproves invalid UTF-8 as the cause |

_Table 4: H3 falsification steps._

**Tooling**: Focused Python invocation using the same incremental decoder as
`relay_stream`.

**Confidence on falsification**: High for the reported characters and failure
mode.

______________________________________________________________________

## Recommended execution order

1. **H1** — directly reproduces the supplied traceback at the narrowest seam.
2. **H2** — settles repository ownership through the call graph and an isolated
   import.
3. **H3** — verifies that the input remains valid UTF-8 before mirroring.

## Termination criteria

- **Root cause identified**: H1 survives its falsification attempts while H2
  and H3 are eliminated, or another hypothesis uniquely survives equivalent
  decisive tests.
- **Escalation trigger**: Revise the hypotheses if the CP1252-like sink does not
  reproduce the exception or the active metadata path enters Cuprum's stream
  implementation.

## Notes for executing agent

Do not modify production code or tests. Use a temporary inline Python program
for the minimal experiment. Report the exception type, complete captured
payload, imported module path, and whether any Cuprum frame or import is needed.

## Investigation results

The falsification experiments and hosted Continuous Integration (CI) evidence
identify Lading's output mirroring as the root cause.

### H1 result: not falsified

A CP1252-like sink raised `UnicodeEncodeError` from `write_to_sink` for a UTF-8
payload containing `ś` and `ń`. When `relay_stream` ran in a real thread with a
payload larger than `_STREAM_CHUNK_SIZE`, the caller continued after `join()`,
the source closed, and only 4,095 of 8,194 characters reached the capture list.
The exception therefore terminates the relay thread and can silently leave
partial captured output.

### H2 result: falsified

The default Cargo metadata path selects Lading's `subprocess_runner`, which
starts `relay_stream` with the parent standard streams. Profiling the focused
reproduction recorded no Cuprum call before the failing write. Cuprum is
imported for Lading's staged programme catalogue, but it is not wired into the
active execution path.

### H3 result: falsified

Lading's incremental decoder produced exact `U+015B` and `U+0144` characters
from the UTF-8 source bytes, with no `U+FFFD` replacement character. The
failure occurs while encoding decoded text to the CP1252 parent sink, not while
decoding Cargo output.

### Hosted evidence

The Windows jobs in `rstest-bdd` CI run 33859545347 use Python 3.14.7 and show
repeated `UnicodeEncodeError` exceptions at `write_to_sink` line 369, called by
`relay_stream` line 320. Both `U+015B` and `U+0144` appear as rejected
characters. The workflow subsequently logs fresh lockfile validation and marks
`Publish dry run` successful, demonstrating that the main caller continues
after the relay threads terminate.

The affected branch pins Lading commit
`e0a8d43fa3d6d7598cad0d4c25883e7ea625feb9`; that commit contains the same
broken-pipe-only exception handling reproduced in the current worktree.

## Conclusion

The root cause belongs to Lading, not Cuprum, Cargo lockfile handling, or the
feature-file rebuild change. The durable repair belongs in
`lading.runtime.subprocess_runner.write_to_sink`: retain ordinary text writes,
handle only `UnicodeEncodeError` with an exact UTF-8 binary-buffer fallback,
and disable mirroring when no binary buffer exists so `relay_stream` keeps
draining and capturing the source. The existing UTF-8 decoder should remain
unchanged.

### Would switching to Cuprum avoid the failure?

For the normal GitHub Actions standard-stream shape, yes. Lading locks Cuprum
v0.1.0, whose `_write_chunk` checks for `sink.buffer` before decoding for a
text write. With a CP1252 text wrapper exposing a binary buffer, it writes the
original UTF-8 subprocess bytes and captures the complete decoded payload.

This does not replace the targeted Lading repair. A Cuprum text-only sink with
no binary buffer raises the same `UnicodeEncodeError`, aborts its consumer, and
does not return captured output. Cuprum also applies one echo setting to both
streams, whereas Lading always mirrors standard error and conditionally mirrors
standard output. Switching runners would therefore be a broader behavioural
migration that incidentally avoids this particular CI stream shape while
leaving Lading's existing subprocess adapter defective.
