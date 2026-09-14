# ADR-005: Emit bounded per-event relay observability logs

## Status

Accepted.

## Date

2026-09-14

## Context and problem statement

`lading.runtime.stream_relay` captures decoded subprocess output independently
of its mirror to a parent stream. When that parent rejects Unicode text, the
relay either selects `sink.buffer` and writes exact UTF-8 bytes or disables a
text-only sink. It also disables mirroring after a broken pipe. These decisions
preserve captured output, but previously gave operators no structured evidence
of why the parent mirror changed state.

The aggregate `lading.utils.metrics` backend is unsuitable for this contract.
It accumulates invocation-wide counters and emits one exit-time summary, while
relay decisions need an immediately observable record with the affected child
stream. Passing output, commands, or raw exception text into those records
would also expose unbounded and potentially sensitive subprocess data.

## Decision

Define `lading.runtime.relay_events.RelayEvent` as a frozen event value object
and emit one `INFO` log record for each relay decision. The message uses the
stable prefix `relay observability event:` and contains the event as its only
parameter.

Every event contains exactly four bounded fields:

| Field            | Stable values                         |
| ---------------- | ------------------------------------- |
| `operation`      | `relay_mirror`                        |
| `stream`         | `stdout`, `stderr`                    |
| `transition`     | `text_to_binary`, `disable_mirroring` |
| `error_category` | `unicode_encode`, `broken_pipe`       |

The relay emits `text_to_binary` with `unicode_encode` when it selects
`sink.buffer`. It emits `disable_mirroring` with `unicode_encode` when a
text-only sink cannot encode output. It emits `disable_mirroring` with
`broken_pipe` at each parent-output broken-pipe handling boundary.

Events must never include subprocess payloads, decoded output, command
arguments, raw exception text, or other unbounded subprocess data.

## Consequences

- Operators can distinguish Unicode fallback, text-only disablement, and
  broken-pipe disablement for each child stream.
- Existing capture and relay return-value semantics remain unchanged because
  event emission observes a decision without altering it.
- Tests pin the exact event fields, cardinality, and payload exclusion.
- Future relay changes must extend the documented literal contract deliberately
  rather than adding unbounded log context.
