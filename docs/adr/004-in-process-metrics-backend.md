# ADR-004: In-process metrics accumulator flushed at exit

## Status

Accepted.

## Date

2026-06-14

## Context and problem statement

`lading` needs to count operational events so maintainers can see how often a
release relied on a particular code path — for example, how often a crates.io
index-lookup failure was downgraded to a warning under
`--allow-unpublished-workspace-deps`, or how lockfile discovery, refresh, and
validation behaved during a `bump`.

A `lading` invocation is a short-lived command-line process, not a long-running
service. There is no scrape endpoint to expose and no daemon lifetime over
which a time series would accumulate. Adding an exporter such as
`prometheus_client` or `statsd` would introduce a runtime dependency and a
network target with no consumer, and would still need a flush at process exit
to be useful for a one-shot command. The logs a `lading` run already emits are
the established operational boundary.

## Decision

Record metrics in an in-process accumulator, `lading.utils.metrics`, and flush
them as a single structured JSON log line at interpreter exit.

- Labelled counters are recorded with `increment_counter(name, **labels)` and
  duration aggregates with `observe_duration(name, seconds, **labels)`.
- `emit_summary` renders the accumulated counters and duration aggregates as
  one `INFO` log line (`lading metrics summary: [...]`). Runs that record
  nothing emit nothing.
- The flush is registered with `atexit` from explicit application bootstrap
  (`lading.cli.main` calls `register_summary_atexit`) rather than as an
  import-time side effect, so the exit-time behaviour is a visible lifecycle
  decision. Registration is idempotent and sets its guard flag only after
  `atexit.register` succeeds.
- The registry doubles as a deterministic test seam through `counter_value`,
  `duration_stats`, `snapshot`, and `reset`.

The metric contracts are documented in the developer guide; the
`publish.index_lookup_downgrade` counter carries `subcommand` (`package` or
`publish`) and `missing_crate` labels.

Label values, including `missing_crate`, are recorded verbatim rather than
bucketed, hashed, or capped. The accumulator is process-local and flushed once,
so its cardinality is bounded by the work a single run performs (at most the
number of distinct crates in that publish run), not by an unbounded external
key space. The crate name is the actionable detail an operator needs, so
collapsing it to a bucket such as `other` would remove the metric's value
without removing a real cost.

## Consequences

- Metrics are dependency-free and add no network target; they are visible only
  in the run's logs, which is where short-lived CLI output is already
  aggregated.
- The verbatim `missing_crate` label is acceptable for the one-shot,
  log-flushed design. If a future change exports these metrics to a long-lived
  time-series backend, the cardinality decision must be revisited and label
  values bucketed or aggregated at the export boundary at that time.
- New metrics should be added to the accumulator and documented in the
  developer guide alongside the existing counters.
