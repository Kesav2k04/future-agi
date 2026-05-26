# Codex review — tracer/utils monitor+graphs chunk (wave 2)

- Date: 2026-05-26T11:20Z
- Branch: feat/ch25-spans-migration
- Scope: tracer/utils/{langfuse_upsert.py, monitor.py, monitor_graphs.py, graphs.py, graphs_optimized.py}
- Reviewed commits (in submission order):
  - `84487e282` — docs(ch25): mark langfuse_upsert.py reads as read-after-write-inside-atomic
  - `e4198f48a` — docs(ch25): defer monitor.py ORM reads — gated on reader extensions
  - `9ec7bf169` — docs(ch25): defer monitor_graphs.py ORM reads — gated on reader extensions
  - `c43415fff` — docs(ch25): defer graphs.py ORM reads — GraphEngine is a generic helper
  - `05b4d7997` — docs(ch25): defer graphs_optimized.py ORM reads — semantic mismatches block migration
- Follow-up commit made in response to this review:
  - `b7f25f28d` — fix(ch25): address codex P2/P3 findings on monitor+graphs CH25-TODO comments

## Codex prompt

> Review my last 5 commits (84487e282, e4198f48a, 9ec7bf169, c43415fff, 05b4d7997) which add CH25-TODO documentation comments to tracer/utils/{langfuse_upsert.py, monitor.py, monitor_graphs.py, graphs.py, graphs_optimized.py}.
>
> These are documentation-only commits — the wave chose to KEEP-PG for all 22 ORM read sites in scope rather than migrate them, after finding that:
> - langfuse_upsert.py reads are inside the same transaction.atomic() as the writes (read-after-write hazard under async dual-write)
> - monitor.py / monitor_graphs.py / graphs_optimized.py ORM sites are PG fallbacks downstream of CH dispatch via MonitorMetricsQueryBuilder / TimeSeriesQueryBuilder / EvalMetricsQueryBuilder; the CH primary path already handles span_attributes_filters / status-stratification / group-by-session/provider via FilterEngine v2
> - graphs.py GraphEngine takes a Django queryset; can't migrate without rewriting callers
> - New time_bucket_aggregate reader (46153d310) takes only observation_type filter and emits sum(cost) bucketed on start_time, whereas these PG paths use parsing_evaltask_filters(monitor.filters) and bucket on created_at with avg(cost)
>
> Focus your review on:
> (1) Are the CH25-TODO comments accurate descriptions of WHY each site is deferred?
> (2) Did I claim a semantic mismatch that doesn't actually exist? (verify time_bucket_aggregate vs TimeSeriesQueryBuilder vs PG ORM cost aggregation, start_time vs created_at)
> (3) For langfuse_upsert.py: is the read-after-write hazard real?
> (4) Did I miss any ORM site that COULD have been migrated?
> (5) Any wrong file:line citations in the CH25-TODO comments?
>
> Return P0/P1/P2/P3 findings with file:line citations.

## Codex findings (verbatim)

**P0:** None.

**P1:** None.

**P2 (3 findings):**

1. `langfuse_upsert.py:198` — read-after-write hazard is real (writes at L182/L246 + reads at L207/L267/L282 inside the `transaction.atomic()` at L58), but the mechanism cited is wrong. Comment named `tracer/services/clickhouse/writer.py` as the async dual-writer; codex found no live call sites for `get_clickhouse_writer()` / `write_span()` outside `writer.py` itself. The active CH architecture is PeerDB CDC from PG → CH (see `tracer/services/clickhouse/__init__.py:4`, `schema.py:6`). TODO should cite CDC / eventual-consistency lag.

2. `graphs_optimized.py:587` — claim that the sibling `TimeSeriesQueryBuilder` universally uses `start_time` was overstated. The builder has two internal paths: the preaggregated `span_metrics_hourly.hour` path (built from `created_at` in the MV at `schema.py:947`) and the raw filtered `start_time`-bucketed path (`time_series.py:286`). Drift (1) — bucket field — exists for the raw path only; drift (2) — `sum(cost)` vs `avg(cost)` — is unique to `time_bucket_aggregate`.

3. `monitor_graphs.py:307` and `graphs_optimized.py:273` — "EvalLogger stays PG / defer until EvalLogger migrates" framing is misleading. There IS a CH `tracer_eval_logger` CDC table (`schema.py:258`), and `MonitorMetricsQueryBuilder` reads it via `EVAL_TABLE` (`monitor_metrics.py:48, 303, 677`). The accurate blocker is "this is a PG fallback / Django subquery shape; the inner span subquery can't be replaced by `time_bucket_aggregate` without rewriting the surrounding EvalLogger aggregation."

**P3 (stale line citations):**

- `langfuse_upsert.py:260` — "lines 182/237" should be "lines 182/246".
- `monitor.py:765` — "line 856+" for the commented-out anomaly detection should be "line 898+".
- `graphs_optimized.py:996` — "L911-987" for CH dispatch should be "L940-987".
- `graphs.py:335` — tracer/views/* import `graphs_optimized`, not `graphs`. The live direct caller of `GraphEngine` is `model_hub/views/separate_evals.py:391`.

**Wave-completeness check (codex):**

> "I did not find a full ORM site that cleanly matches `time_bucket_aggregate` as-is. The closest sites are the system metric bucket helpers, but `created_at` vs `start_time`, `Avg(cost)` vs `sum(cost)`, full `parsing_evaltask_filters`, status counts, and group-by session/provider keep them outside that reader's current contract."

Confirms the KEEP-PG decision for all 22 sites.

## Actions taken

### P2 — all 3 accepted, fixed in `b7f25f28d`

- `langfuse_upsert.py`: mechanism comment now cites PeerDB CDC replication lag (per `tracer/services/clickhouse/__init__.py:4`), not the inactive `writer.py` module.
- `graphs_optimized.py:587`: comment now distinguishes the preaggregated MV path (matches PG `created_at`) from the raw filtered path (matches CH `start_time`), and identifies drift (2) as unique to `time_bucket_aggregate`.
- `monitor_graphs.py:307`, `monitor_graphs.py:468`, `graphs_optimized.py:273`: comment now correctly identifies the blocker as "PG fallback / Django subquery shape; inner span subquery isn't naturally replaceable by `time_bucket_aggregate` without rewriting the EvalLogger aggregation around it," and acknowledges the existing CH `tracer_eval_logger` CDC table.

### P3 — all 4 accepted, fixed in `b7f25f28d`

- `langfuse_upsert.py:260` line range corrected to 182/246.
- `monitor.py:766` anomaly-detection line corrected to 898+.
- `graphs_optimized.py:996` CH dispatch range corrected to 940-987.
- `graphs.py:335` caller list trimmed to the actual live direct caller (`model_hub/views/separate_evals.py:391`); the tracer/views/* imports were of `graphs_optimized`, not `graphs`.

### Wave-completeness — accepted

Codex agreed: no ORM site in this wave's scope cleanly matches `time_bucket_aggregate`. No additional commits required.

## Deferred sites (22 total)

| File | Sites | Reason |
|---|---|---|
| `langfuse_upsert.py` | 3 reads | Read-after-write inside `transaction.atomic()` + PeerDB CDC replication lag. CH read would silently miss just-written spans → 0-latency root spans / dropped EvalLoggers. Plus Django FK to `EvalLogger.observation_span` requires Django model instance. |
| `monitor.py` | 8 sites | All PG fallback (after CH dispatch via `MonitorMetricsQueryBuilder`) or dead code (`_get_time_series_df_for_other_metrics` consumed only by commented-out `_check_anomaly_detection_threshold`). |
| `monitor_graphs.py` | 6 sites | All PG fallback (after CH dispatch via `MonitorMetricsQueryBuilder.build_time_series_query`). 4 ObservationSpan, 2 cross-store EvalLogger+ObservationSpan. |
| `graphs.py` | 3 sites | Generic helper (`GraphEngine`) that takes a passed-in Django queryset; can't migrate without rewriting callers. Only live direct caller: `model_hub/views/separate_evals.py:391`. |
| `graphs_optimized.py` | 5 sites | 1 EvalLogger subquery shape + 1 multi-metric helper with sum/avg + start_time/created_at semantic drift + 3 in PG fallback under CH `TimeSeriesQueryBuilder` dispatch (with explicit "NO ID MATERIALIZATION" memory optimization). |

## Reader-method signatures requested (for the migration owner)

Five new methods would unblock most of the deferred sites:

1. **`time_bucket_aggregate_with_filters(project_id, *, interval, since, until, **parsing_evaltask_filters_for_ch_output)`** — accepts the full `parsing_evaltask_filters_for_ch` output (span_attributes_filters via FilterEngine, observation_type, session_id, date_range, created_at, project_id) and emits an additional `error_count` column alongside the existing `span_count` / `tokens` / `cost` / `latency_ms`. Primary unblock for monitor.py / monitor_graphs.py bucket sites.

2. **`aggregate_window_with_filters(project_id, *, since, until, **filter_kwargs)`** — single-bucket variant of (1) for the `_get_metric_value` scalar path in monitor.py.

3. **`group_by_session_window(project_id, *, since, until, **filter_kwargs)` / `group_by_provider_window(...)`** — emit `[{bucket, session_id|provider, total, error_count}, ...]` for the ERROR_FREE_SESSION_RATES / SERVICE_PROVIDER_ERROR_RATES paths in monitor.py and monitor_graphs.py.

4. **`multi_metric_time_bucket_aggregate(project_id, *, interval, since, until, cost_agg="avg"|"sum", bucket_field="start_time"|"created_at")`** — the "one CH query, three user-visible metrics" shape that `get_all_system_metrics` needs. Existing `time_bucket_aggregate` is close but defaults bury the semantic decisions inside the function rather than exposing them.

5. **`iter_rows_with_filters(project_id, **filter_kwargs)`** — raw `(timestamp, status, latency_ms, ...)` row iterator for the Prophet anomaly-detection helpers in monitor.py. Lower priority since anomaly detection itself is currently disabled.
