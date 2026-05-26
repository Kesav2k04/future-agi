# Wave-3 monitor + graphs migration — STOP and REPORT

- Date: 2026-05-26T12:28Z
- Branch: feat/ch25-spans-migration
- HEAD before this report: 93c5c415f
- Scope considered: `tracer/utils/{monitor.py, monitor_graphs.py, graphs.py, graphs_optimized.py, langfuse_upsert.py}` — 27 ORM sites total
- Decision: NO migrations performed. Wave-3 reader extensions are
  necessary but NOT SUFFICIENT to migrate the 22 monitor/graph sites
  cleanly; langfuse_upsert.py's 5 sites stay KEEP-PG by atomic-block
  + FK constraint (already documented).

## Why STOP

Wave-2 codex review (`REVIEWS/codex-monitor-graphs-chunk-20260526T1120.md`)
already analyzed these exact 22 ORM sites and KEPT-PG all of them, with
codex returning **P0=None, P1=None**. Codex's verdict:

> "I did not find a full ORM site that cleanly matches `time_bucket_aggregate`
> as-is. The closest sites are the system metric bucket helpers, but
> `created_at` vs `start_time`, `Avg(cost)` vs `sum(cost)`, full
> `parsing_evaltask_filters`, status counts, and group-by session/provider
> keep them outside that reader's current contract."

Wave-3 (`93c5c415f`) added 9 new reader methods covering items (1) and
(2) of the codex reader-extension request list, but **NOT** items (3),
(4), (5). The remaining gaps are load-bearing for every site in this
scope. Migrating now would re-introduce the P2 semantic drifts that
wave-2 codex flagged.

## Primary-source evidence (not hearsay)

### Gap 1 — bucket field drift (P1 if migrated)

PG fallback paths bucket on `created_at`:

- `tracer/utils/monitor.py:293,514` — `ObservationSpan.objects.filter(project=monitor.project, created_at__range=(start_time, end_time))` + `Trunc("created_at", interval_kind, ...)`.
- `tracer/utils/monitor.py:514` — `Trunc("created_at", interval_kind, output_field=DateTimeField())`.
- `tracer/utils/monitor_graphs.py:216,355,412,564,624` — `Extract("created_at", "epoch")` for ToTimestamp bucket annotation.
- `tracer/utils/graphs_optimized.py:622,1068,1076,1084` — `.annotate(time_bucket=trunc_func("created_at"))`.
- `tracer/utils/eval_tasks.py:170,172` — `parsing_evaltask_filters` produces `Q(created_at__range=...)` and `Q(created_at__gte=...)`.

Wave-3 `time_bucket_aggregate_with_filters` hardcodes bucket + window on
`start_time`:

```
# tracer/services/clickhouse/v2/span_reader.py:823
where = ["is_deleted = 0", "start_time >= %(since)s", "start_time <  %(until)s"]
...
# line 847
f"SELECT {bucket_fn}(start_time) AS bucket, "
```

`start_time` and `created_at` are different columns on the CH spans v2
schema (`tracer/services/clickhouse/v2/schema/002_spans_v2.sql:57,129`).
For backfilled or replayed spans, ingestion lag makes these diverge by
arbitrary amounts. A migration that silently switches the bucket column
will produce different bucket membership counts under any ingestion-lag
condition — codex flagged this as P2 in wave-2 (`graphs_optimized.py:587`
comment, ratified by codex).

### Gap 2 — `parsing_evaltask_filters` Q-object cannot pass through (P0 if migrated)

`tracer/utils/eval_tasks.py:136-176` produces a Django `Q` that includes:

- `span_attributes_filters` → routes through `FilterEngine.get_filter_conditions_for_span_attributes(value)` (`eval_tasks.py:152`). Not representable in flat kwargs.
- `observation_type` (representable)
- `session_id` → resolves to `Q(trace_id__in=Trace.objects.filter(session_id=value).values_list("id"))` (`eval_tasks.py:165`). Could be re-expressed using CHSpanReader if we resolve trace_ids upstream.
- `date_range` → uses `created_at` (Gap 1).
- `created_at` → uses `created_at` (Gap 1).
- `project_id` (representable).

`monitor.filters` carries `span_attributes_filters` in production —
verified by `tracer/tests/test_monitor.py:150,168,186` (canonical
filter shape includes `customer_tier` SPAN_ATTRIBUTE predicate).

The wave-3 `time_bucket_aggregate_with_filters` kwargs are
`project_id, trace_ids, observation_type, session_id, status_filter`
— no FilterEngine v2 passthrough. Any monitor that has a non-empty
`span_attributes_filters` would silently match more rows than the PG
path. Codex would flag tenant-scope erosion (P0 in their wave-2 anti-
pattern list).

### Gap 3 — ERROR_FREE_SESSION_RATES needs per-session breakdown, not per-bucket aggregate

Task suggested: "load total per session AND error per session via two
`time_bucket_aggregate_with_filters` calls, compute ratio in Python."
This does not produce the correct semantics.

PG path (`monitor.py:315-333`):
```python
result = (
    base_queryset.exclude(trace__session__isnull=True)
    .values("trace__session")                            # group BY session
    .annotate(error_count=Count("id", filter=Q(status="ERROR")))
    .aggregate(
        total_sessions=Count("trace__session"),
        error_free_sessions=Count("trace__session", filter=Q(error_count=0)),
    )
)
```
The output is "how many sessions had zero error spans". This requires
per-session error count, then a count of sessions where that count is
zero — a fundamentally two-level aggregation.

`time_bucket_aggregate_with_filters` emits per-BUCKET aggregates
(`bucket, span_count, error_count`). It has no `session_id` grouping
dimension in the output. Two calls of it (one with `status_filter=
"error"`, one without) give per-bucket span counts, not per-session
error breakdown. The session-level error-free ratio is not derivable
from these.

Codex's wave-2 request item (3) explicitly proposed
`group_by_session_window(project_id, *, since, until, **filter_kwargs)`
returning `[{bucket, session_id, total, error_count}, ...]` for this
exact path. Wave-3 didn't add it.

### Gap 4 — `cost_agg` knob missing for graphs_optimized.get_all_system_metrics

PG path (`graphs_optimized.py:622-631`):
```python
.annotate(
    latency_value=Avg("latency_ms"),
    tokens_value=models.Sum("total_tokens"),
    cost_value=Avg("cost"),      # ← Avg, not Sum
    count=Count("id"),
)
```

Wave-3 `time_bucket_aggregate_with_filters` returns `cost = sum(cost)`
per bucket (`span_reader.py:850`). The PG helper does `Avg("cost")`.
Codex wave-2 P2 ratified this drift as load-bearing
(`graphs_optimized.py:587` comment).

### Gap 5 — graphs_optimized's "NO ID MATERIALIZATION" subquery shape

`tracer/utils/graphs_optimized.py:1022,1039,1053` use
`trace_id__in=trace_ids_queryset.values("id")` — a Django subquery
that PG's query compiler folds into the same SQL statement (no Python-
side id list materialization). Replacing with CHSpanReader requires
materializing the id list, defeating an existing memory optimization
called out inline ("NO ID MATERIALIZATION" comment at `graphs_optimized
.py:996`). Codex wave-2 ratified KEEP-PG here.

### Gap 6 — graphs.py GraphEngine takes Django queryset as input

`GraphEngine.__init__` (`graphs.py:21-43`) accepts `objects` as a
Django queryset constructed by the caller (live caller:
`model_hub/views/separate_evals.py:391`). Refactoring requires
changing the caller's signature from "build queryset, hand to
GraphEngine" to "ask CH for buckets, hand to GraphEngine". That is a
caller-side refactor, not a 1-line ORM-call swap.

## langfuse_upsert.py decision: KEEP-PG (all 5 sites)

All 5 ORM reads happen inside the `with transaction.atomic():` block
that opens at `tracer/utils/langfuse_upsert.py:58` and closes at the
end of `upsert_langfuse_trace` (line 305).

Sites + classification:
| Line | Read | Inside atomic? | Reason |
|---|---|---|---|
| 71  | `Trace.no_workspace_objects.filter(...).first()` | Yes | Read-before-update on FK-target row. Spans get written after. |
| 98  | `EndUser.no_workspace_objects.get_or_create(...)` | Yes | get_or_create — read+write on the same model. |
| 116 | `TraceSession.no_workspace_objects.get_or_create(...)` | Yes | Same. |
| 209 | `ObservationSpan.no_workspace_objects.filter(trace=trace).exclude(id=root_span_id).aggregate(...)` | Yes — reads spans JUST written at line 182 | Read-after-write. CH lags via PeerDB CDC → would miss the just-written spans, producing a 0-latency root. |
| 270 | `ObservationSpan.no_workspace_objects.get(id=observation_id)` | Yes | Read used as Django FK target for `EvalLogger.observation_span` — CHSpan is not a Django model instance. |
| 285 | `ObservationSpan.no_workspace_objects.filter(trace=trace).order_by("start_time").first()` | Yes | Same FK constraint + same read-after-write hazard. |

All sites already carry the correct CH25-TODO comments documenting
this (`langfuse_upsert.py:198,262,280`).

## Reader extensions still needed (matches wave-2 codex's request list)

Wave-3 landed items (1) `time_bucket_aggregate_with_filters` and (2)
`aggregate_window_with_filters`. The following items from
`REVIEWS/codex-monitor-graphs-chunk-20260526T1120.md:90-103` are still
required to migrate the deferred sites:

### Required for monitor.py / monitor_graphs.py / graphs_optimized.py

**(A) `bucket_field=` knob** on `time_bucket_aggregate_with_filters`
and `aggregate_window_with_filters`:

```python
def time_bucket_aggregate_with_filters(
    self, *, interval, since, until,
    bucket_field: Literal["start_time", "created_at"] = "start_time",
    ...
) -> list[dict]:
    ...
    # SELECT {bucket_fn}({bucket_field}) AS bucket, ...
    # WHERE ... AND {bucket_field} >= %(since)s AND {bucket_field} < %(until)s
```

`created_at` exists on CH spans v2 (`002_spans_v2.sql:129`); the change
is mechanical. Unblocks every monitor.py / monitor_graphs.py /
graphs_optimized.py site that buckets on `created_at`.

**(B) `cost_agg=` knob** on the same reader:

```python
cost_agg: Literal["sum", "avg"] = "sum"
```

Reader emits `sum(cost) AS cost` today; PG helpers in
`graphs_optimized.py:627` want `avg(cost)`. Without the knob, migrating
`get_all_system_metrics` changes user-visible cost values.

**(C) `span_attributes_filter` passthrough**, threading the FilterEngine
v2 fragment into the WHERE clause:

```python
def time_bucket_aggregate_with_filters(
    self, *, ...,
    span_attributes_filters: Optional[list[dict]] = None,
) -> list[dict]:
    ...
    if span_attributes_filters:
        fragment, params2 = FilterEngineV2.compile_for_ch(
            span_attributes_filters
        )
        where.append(fragment)
        params.update(params2)
```

This is the harder lift — needs FilterEngine v2's CH compiler. Without
it, every monitor that has `span_attributes_filters` (live shape per
`test_monitor.py:150,186`) will silently miss the predicate.

**(D) `group_by_session_window` and `group_by_provider_window`**
returning `[{bucket, session_id|provider, total, error_count}, ...]`.
Required for the `ERROR_FREE_SESSION_RATES` and
`SERVICE_PROVIDER_ERROR_RATES` paths in monitor.py and monitor_graphs.py.
The reader currently can't produce per-session-per-bucket breakdowns.

### Required for graphs_optimized.py large-fanout paths only

**(E)** A reader that accepts `trace_ids: list[str]` AND
`bucket_field="created_at"` AND `cost_agg="avg"` AND status-stratified
counts. Currently `trace_ids` is supported but only with `bucket_field=
start_time` and `cost_agg=sum`. The "NO ID MATERIALIZATION" optimization
is intrinsically PG-only (CH always materializes the IN list), so
graphs_optimized.py's "charts" branch should stay KEEP-PG until the
underlying PG fallback path itself is removed.

### Required for graphs.py

**(F)** Caller-side refactor of `model_hub/views/separate_evals.py:391`
to ask CH for buckets directly instead of building a Django queryset
and handing it to GraphEngine. Not a reader extension; a caller
migration. Defer until after this wave.

## Why NOT migrate anyway

The task explicitly permits "STOP and report if needed." Migrating any
of the 22 sites now would either:
1. Re-introduce a P2 drift codex already flagged in wave-2 (gap 1, 4)
2. Silently miss filter predicates (gap 2) — a P0 by codex's wave-2
   anti-pattern list (tenant scope erosion / silent missing rows)
3. Produce mathematically incorrect output (gap 3 — ERROR_FREE_SESSION_
   RATES with the suggested two-bucket-call approach)
4. Defeat an existing memory optimization (gap 5)

None of these failure modes pass codex's wave-2 P0/P1 anti-pattern
checks. The right path is to land the missing reader signatures first.

## Recommendation

Reverse the dependency: land readers (A)–(D) above as a wave-3 follow-up
(call it wave-3.5), then re-run the monitor + graphs + graphs_optimized
migration with the updated reader surface. Items (E) and (F) can stay
KEEP-PG until the PG fallback is removable.

For now, the 22 sites stay KEEP-PG with the wave-2 CH25-TODO comments
unchanged. langfuse_upsert.py's 5 sites stay KEEP-PG by atomic + FK,
also unchanged.

## Files NOT modified

- `tracer/utils/monitor.py`
- `tracer/utils/monitor_graphs.py`
- `tracer/utils/graphs.py`
- `tracer/utils/graphs_optimized.py`
- `tracer/utils/langfuse_upsert.py`
- `tracer/services/clickhouse/v2/span_reader.py`

No commits added by this agent.
