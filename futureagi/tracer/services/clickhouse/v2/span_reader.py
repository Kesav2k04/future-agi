"""
ch_span_reader — read API the eval runner can call to load spans directly from CH.

Drop-in for the existing Django ORM access in tracer/utils/eval.py:

    PG path (today):
        observation_span = ObservationSpan.objects.get(id=span_id)
        spans = ObservationSpan.objects.filter(trace=trace, deleted=False)

    CH path (target post-cutover):
        reader = CHSpanReader(host=..., port=...)
        observation_span = reader.get(span_id)
        spans = reader.list_by_trace(trace_id)

The shapes match the Django model fields the eval runner actually touches
(see grep -n "observation_span[.]" tracer/utils/eval.py for the surface).

Design goals:
  • SAME FIELD NAMES as the Django model, so eval code can be swapped over
    with a one-line `.objects.get(id=...)` → `reader.get(...)` change.
  • Frozen dataclasses so callers cannot accidentally mutate (CH is the
    authoritative store; the read path is meant to be pure).
  • Single small query per call (no N+1 joins). The CH schema denormalizes
    trace_session_id / org_id / project_version_id onto each span row, so
    most eval reads need exactly one row from `spans FINAL`.

CRITICAL non-goal: write back. Eval results (EvalLogger rows) still go to
PG until that's also migrated to CH (separate task). This reader is
read-only.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Optional

import clickhouse_connect


# Field list that the eval runner actually reads off of an ObservationSpan.
# Mirrored from the grep:
#   tracer/utils/eval.py:725, 1108, 1493, 1578, 1711  → .get(id=...)
#   tracer/utils/eval.py:210, 219, 271, 289, 306, 2218 → .filter(...) aggregates
# Adding a field here is cheap; removing one is a breaking change for callers.
@dataclass(frozen=True)
class CHSpan:
    id: str
    project_id: str
    trace_id: str
    parent_span_id: str
    name: str
    observation_type: str
    operation_name: str

    start_time: datetime
    end_time: Optional[datetime]
    latency_ms: int

    model: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost: float

    status: str
    status_message: str

    org_id: Optional[str]
    project_version_id: Optional[str]
    end_user_id: Optional[str]
    trace_session_id: Optional[str]
    prompt_version_id: Optional[str]
    prompt_label_id: Optional[str]
    custom_eval_config_id: Optional[str]

    # Inputs / outputs come back as raw JSON-strings from CH; the eval runner
    # currently calls json.loads on them where needed. Keep the shape identical
    # so no downstream `.input` callsite changes.
    input: str
    output: str
    tags: str
    span_events: str
    metadata: str                                                   # JSON string from CH typed JSON column
    resource_attrs: str                                             # JSON string
    attributes_extra: str                                           # JSON string

    # Typed Map columns. Maps to Python dicts.
    attrs_string: dict[str, str] = field(default_factory=dict)
    attrs_number: dict[str, float] = field(default_factory=dict)
    attrs_bool: dict[str, int] = field(default_factory=dict)

    # Derived hot columns (materialized in the CH schema)
    llm_request_model: str = ""
    llm_response_model: str = ""
    embedding_model: str = ""
    streaming: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None

    eval_status: str = ""
    semconv_source: str = ""
    is_deleted: int = 0


# Stable column ordering for the CH query. JSON columns wrapped in toJSONString
# so clickhouse-connect can decode them (it cannot yet handle the typed JSON
# column type in result rows — see DECISIONS #015, #018 of the migration).
_READ_COLUMNS: tuple[str, ...] = (
    "id", "toString(project_id) AS project_id", "trace_id", "parent_span_id",
    "name", "observation_type", "operation_name",
    "start_time", "end_time", "latency_ms",
    "model", "provider", "prompt_tokens", "completion_tokens", "total_tokens", "cost",
    "status", "status_message",
    "toString(org_id) AS org_id", "toString(project_version_id) AS project_version_id",
    "toString(end_user_id) AS end_user_id", "toString(trace_session_id) AS trace_session_id",
    "toString(prompt_version_id) AS prompt_version_id",
    "toString(prompt_label_id) AS prompt_label_id",
    "toString(custom_eval_config_id) AS custom_eval_config_id",
    "input", "output", "tags", "span_events",
    "toJSONString(metadata) AS metadata",
    "toJSONString(resource_attrs) AS resource_attrs",
    "toJSONString(attributes_extra) AS attributes_extra",
    "attrs_string", "attrs_number", "attrs_bool",
    "llm_request_model", "llm_response_model", "embedding_model",
    "streaming", "temperature", "top_p", "max_tokens",
    "eval_status", "semconv_source", "is_deleted",
)

_SELECT_SQL = ", ".join(_READ_COLUMNS)

# Order in which result_rows columns arrive — bare names (no `AS` aliases) for the
# row→dataclass mapping below.
_DATA_KEYS: tuple[str, ...] = (
    "id", "project_id", "trace_id", "parent_span_id",
    "name", "observation_type", "operation_name",
    "start_time", "end_time", "latency_ms",
    "model", "provider", "prompt_tokens", "completion_tokens", "total_tokens", "cost",
    "status", "status_message",
    "org_id", "project_version_id", "end_user_id", "trace_session_id",
    "prompt_version_id", "prompt_label_id", "custom_eval_config_id",
    "input", "output", "tags", "span_events",
    "metadata", "resource_attrs", "attributes_extra",
    "attrs_string", "attrs_number", "attrs_bool",
    "llm_request_model", "llm_response_model", "embedding_model",
    "streaming", "temperature", "top_p", "max_tokens",
    "eval_status", "semconv_source", "is_deleted",
)


def _row_to_chspan(row: tuple) -> CHSpan:
    d = dict(zip(_DATA_KEYS, row))
    # CH returns the toString() forms with literal 'NULL' for missing UUIDs in
    # some 25.x patch versions; normalize either case to None.
    for k in ("org_id", "project_version_id", "end_user_id", "trace_session_id",
              "prompt_version_id", "prompt_label_id", "custom_eval_config_id"):
        v = d.get(k)
        d[k] = None if v in (None, "", "00000000-0000-0000-0000-000000000000") else v
    return CHSpan(**d)


class CHSpanReader:
    """Read-only span fetcher backed by ClickHouse `spans FINAL`.

    Thread-safe. Holds a clickhouse-connect HTTP client; safe to share between
    threads but each `query` call holds the connection briefly. For concurrent
    high-fanout reads (parallel eval runners) instantiate one reader per worker.
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 19001,
        username: str = "default",
        password: str = "",
        database: str = "default",
        timeout_sec: int = 30,
    ):
        self._client = clickhouse_connect.get_client(
            host=host, port=port, username=username, password=password,
            database=database, send_receive_timeout=timeout_sec,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ─── Single-row by id ────────────────────────────────────────────────────
    def get(self, span_id: str) -> Optional[CHSpan]:
        """Equivalent to ObservationSpan.objects.get(id=span_id), returns None
        if absent (matches the pattern most callers wrap with try/except)."""
        rows = self._client.query(
            f"SELECT {_SELECT_SQL} FROM spans FINAL "
            "WHERE id = %(span_id)s AND is_deleted = 0 LIMIT 1",
            parameters={"span_id": span_id},
        ).result_rows
        if not rows:
            return None
        return _row_to_chspan(rows[0])

    # ─── All spans in a trace ────────────────────────────────────────────────
    def list_by_trace(self, trace_id: str) -> list[CHSpan]:
        """Equivalent to ObservationSpan.objects.filter(trace=trace, deleted=False).

        Returned in start_time, id order so the eval runner's trace-walking
        logic sees spans in a deterministic chronological order.
        """
        rows = self._client.query(
            f"SELECT {_SELECT_SQL} FROM spans FINAL "
            "WHERE trace_id = %(trace_id)s AND is_deleted = 0 "
            "ORDER BY start_time, id",
            parameters={"trace_id": trace_id},
        ).result_rows
        return [_row_to_chspan(r) for r in rows]

    # ─── All spans in a session ──────────────────────────────────────────────
    def list_by_session(self, session_id: str) -> list[CHSpan]:
        """For session-level evals (`EvalLogger.target_type='session'`)."""
        rows = self._client.query(
            f"SELECT {_SELECT_SQL} FROM spans FINAL "
            "WHERE trace_session_id = %(session_id)s AND is_deleted = 0 "
            "ORDER BY start_time, id",
            parameters={"session_id": session_id},
        ).result_rows
        return [_row_to_chspan(r) for r in rows]

    # ─── Bulk fetch by trace ids ──────────────────────────────────────────────
    def list_by_trace_ids(self, trace_ids: list[str]) -> list[CHSpan]:
        """Equivalent to ObservationSpan.objects.filter(trace_id__in=trace_ids).

        Returns spans across multiple traces in (trace_id, start_time, id) order.
        Empty input returns empty list.
        """
        if not trace_ids:
            return []
        rows = self._client.query(
            f"SELECT {_SELECT_SQL} FROM spans FINAL "
            "WHERE trace_id IN %(trace_ids)s AND is_deleted = 0 "
            "ORDER BY trace_id, start_time, id",
            parameters={"trace_ids": tuple(trace_ids)},
        ).result_rows
        return [_row_to_chspan(r) for r in rows]

    # ─── Children of a parent span ────────────────────────────────────────────
    def list_by_parent(self, parent_span_id: str, *, limit: Optional[int] = None) -> list[CHSpan]:
        """Equivalent to ObservationSpan.objects.filter(parent_span_id=, deleted=False)
        ordered by start_time, id. `limit` caps the result for display-list paths
        (e.g. `[:20]` slices that the AI-tools `get_span` does)."""
        lim_clause = f" LIMIT {int(limit)}" if limit else ""
        rows = self._client.query(
            f"SELECT {_SELECT_SQL} FROM spans FINAL "
            "WHERE parent_span_id = %(parent)s AND is_deleted = 0 "
            f"ORDER BY start_time, id{lim_clause}",
            parameters={"parent": parent_span_id},
        ).result_rows
        return [_row_to_chspan(r) for r in rows]

    # ─── Spans by id batch ────────────────────────────────────────────────────
    def list_by_ids(self, span_ids: list[str]) -> list[CHSpan]:
        """Equivalent to ObservationSpan.objects.filter(id__in=span_ids).

        Result order is NOT preserved relative to the input list (CH orders
        by id for determinism). Callers that need a specific order should
        sort the result themselves.
        """
        if not span_ids:
            return []
        rows = self._client.query(
            f"SELECT {_SELECT_SQL} FROM spans FINAL "
            "WHERE id IN %(ids)s AND is_deleted = 0 "
            "ORDER BY id",
            parameters={"ids": tuple(span_ids)},
        ).result_rows
        return [_row_to_chspan(r) for r in rows]

    # ─── Aggregations across many traces ──────────────────────────────────────
    def aggregate_by_trace_ids(self, trace_ids: list[str]) -> dict[str, Any]:
        """Sum(tokens, cost) + count across multiple traces in one query.

        Used by AI tools that compute trace-list totals (e.g. get_trace_timeline
        bucketing spans by time). Returns a single aggregate row across all
        input trace_ids; for per-trace aggregates use trace_aggregate() in a
        loop or extend this method later if a real call site needs it.
        """
        if not trace_ids:
            return {"span_count": 0, "prompt_tokens": 0, "completion_tokens": 0,
                    "total_tokens": 0, "cost": 0.0}
        rows = self._client.query(
            "SELECT count() AS n, "
            "sum(prompt_tokens) AS pt, sum(completion_tokens) AS ct, "
            "sum(total_tokens) AS tt, sum(cost) AS cost "
            "FROM spans FINAL "
            "WHERE trace_id IN %(trace_ids)s AND is_deleted = 0",
            parameters={"trace_ids": tuple(trace_ids)},
        ).result_rows
        if not rows:
            return {"span_count": 0, "prompt_tokens": 0, "completion_tokens": 0,
                    "total_tokens": 0, "cost": 0.0}
        n, pt, ct, tt, c = rows[0]
        return {
            "span_count": int(n or 0),
            "prompt_tokens": int(pt or 0),
            "completion_tokens": int(ct or 0),
            "total_tokens": int(tt or 0),
            "cost": float(c or 0.0),
        }

    # ─── Session-level aggregation ────────────────────────────────────────────
    def session_aggregate(self, session_id: str) -> dict[str, Any]:
        """Same shape as trace_aggregate but scoped by trace_session_id. Used by
        get_session_analytics + the session detail view. Includes the start/end
        bracket so callers can compute session duration."""
        rows = self._client.query(
            "SELECT count() AS n, "
            "sum(prompt_tokens) AS pt, sum(completion_tokens) AS ct, "
            "sum(total_tokens) AS tt, sum(cost) AS cost, "
            "min(start_time) AS start_time, max(end_time) AS end_time "
            "FROM spans FINAL "
            "WHERE trace_session_id = %(sid)s AND is_deleted = 0",
            parameters={"sid": session_id},
        ).result_rows
        if not rows:
            return {}
        n, pt, ct, tt, c, st, et = rows[0]
        return {
            "span_count": int(n or 0),
            "prompt_tokens": int(pt or 0),
            "completion_tokens": int(ct or 0),
            "total_tokens": int(tt or 0),
            "cost": float(c or 0.0),
            "start_time": st,
            "end_time": et,
        }

    # ─── Project-scoped fetches ───────────────────────────────────────────────
    def list_by_project(
        self,
        project_id: str,
        *,
        observation_type: Optional[str] = None,
        project_version_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[CHSpan]:
        """Equivalent to ObservationSpan.objects.filter(project_id=, ...).

        Keyword filters compose as ANDs. Used by views that scope spans to a
        single project (delete cascades, project_version queries, etc.).
        `limit` caps the result for paginated paths.
        """
        where = ["is_deleted = 0", "project_id = %(pid)s"]
        params: dict[str, Any] = {"pid": project_id}
        if observation_type:
            where.append("observation_type = %(otype)s")
            params["otype"] = observation_type
        if project_version_id:
            where.append("project_version_id = %(pvid)s")
            params["pvid"] = project_version_id
        lim_clause = f" LIMIT {int(limit)}" if limit else ""
        rows = self._client.query(
            f"SELECT {_SELECT_SQL} FROM spans FINAL "
            f"WHERE {' AND '.join(where)} "
            f"ORDER BY start_time, id{lim_clause}",
            parameters=params,
        ).result_rows
        return [_row_to_chspan(r) for r in rows]

    def count_by_project(
        self,
        project_id: str,
        *,
        observation_type: Optional[str] = None,
        project_version_id: Optional[str] = None,
    ) -> int:
        """Count of spans matching the project + optional filters. Equivalent
        to ObservationSpan.objects.filter(project_id=, ...).count()."""
        where = ["is_deleted = 0", "project_id = %(pid)s"]
        params: dict[str, Any] = {"pid": project_id}
        if observation_type:
            where.append("observation_type = %(otype)s")
            params["otype"] = observation_type
        if project_version_id:
            where.append("project_version_id = %(pvid)s")
            params["pvid"] = project_version_id
        rows = self._client.query(
            "SELECT count() FROM spans FINAL " f"WHERE {' AND '.join(where)}",
            parameters=params,
        ).result_rows
        return int(rows[0][0]) if rows else 0

    def count_by_trace(self, trace_id: str) -> int:
        """Equivalent to ObservationSpan.objects.filter(trace_id=).count()."""
        rows = self._client.query(
            "SELECT count() FROM spans FINAL "
            "WHERE trace_id = %(trace_id)s AND is_deleted = 0",
            parameters={"trace_id": trace_id},
        ).result_rows
        return int(rows[0][0]) if rows else 0

    def exists_for_trace(self, trace_id: str) -> bool:
        """Equivalent to ObservationSpan.objects.filter(trace_id=).exists()."""
        return self.count_by_trace(trace_id) > 0

    # ─── Per-trace aggregates (replaces feed.py / project_version.py rollups) ─
    def per_trace_aggregate(self, trace_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Equivalent to:
            ObservationSpan.objects.filter(trace_id__in=trace_ids)
                .values("trace_id")
                .annotate(span_count=Count("id"), prompt_tokens=Sum(...),
                          completion_tokens=Sum(...), total_tokens=Sum(...),
                          cost=Sum(...), start_time=Min(...), end_time=Max(...),
                          latency_ms=Sum(...))

        Returns a dict keyed by trace_id (str). Missing trace_ids return an
        empty entry (use .get(tid, {}) for safety). Empty input returns {}.
        """
        if not trace_ids:
            return {}
        rows = self._client.query(
            "SELECT toString(trace_id) AS tid, "
            "count() AS n, "
            "sum(prompt_tokens) AS pt, sum(completion_tokens) AS ct, "
            "sum(total_tokens) AS tt, sum(cost) AS cost, "
            "min(start_time) AS st, max(end_time) AS et, "
            "sum(latency_ms) AS lat "
            "FROM spans FINAL "
            "WHERE trace_id IN %(tids)s AND is_deleted = 0 "
            "GROUP BY toString(trace_id)",
            parameters={"tids": tuple(trace_ids)},
        ).result_rows
        return {
            tid: {
                "span_count": int(n or 0),
                "prompt_tokens": int(pt or 0),
                "completion_tokens": int(ct or 0),
                "total_tokens": int(tt or 0),
                "cost": float(c or 0.0),
                "start_time": st,
                "end_time": et,
                "latency_ms": int(lat or 0),
            }
            for (tid, n, pt, ct, tt, c, st, et, lat) in rows
        }

    # ─── Root-span start_time per trace (replay_session ordering helper) ─────
    def per_trace_root_span_start_times(
        self, trace_ids: list[str]
    ) -> dict[str, Optional[datetime]]:
        """Equivalent to:
            Subquery(ObservationSpan.objects.filter(trace_id=OuterRef("id"),
                                                     parent_span_id__isnull=True)
                                              .values("start_time")[:1])

        Returns a dict trace_id → start_time of the trace's root span (or
        None if no root span exists in CH yet). Used to order trace lists
        by their first activity time without joining cross-store.

        CH stores parent_span_id as non-nullable String (schema 001); root
        spans have an empty string. We pick min(start_time) for ties.
        """
        if not trace_ids:
            return {}
        rows = self._client.query(
            "SELECT toString(trace_id) AS tid, min(start_time) AS st "
            "FROM spans FINAL "
            "WHERE trace_id IN %(tids)s AND is_deleted = 0 "
            "  AND parent_span_id = '' "
            "GROUP BY toString(trace_id)",
            parameters={"tids": tuple(trace_ids)},
        ).result_rows
        result: dict[str, Optional[datetime]] = {tid: None for tid in trace_ids}
        for tid, st in rows:
            result[tid] = st
        return result

    # ─── Distinct end_users per trace (feed user-count rollup) ────────────────
    def distinct_end_users_by_trace_ids(
        self, trace_ids: list[str]
    ) -> dict[str, set[str]]:
        """Equivalent to:
            ObservationSpan.objects.filter(trace_id__in=trace_ids,
                                            end_user__isnull=False)
                .values("trace_id", "end_user_id").distinct()
            grouped into {trace_id: {end_user_id, ...}}

        Pushes DISTINCT into CH so we don't materialize all spans Python-
        side just to count distinct users. Empty trace_ids returns {}.
        """
        if not trace_ids:
            return {}
        rows = self._client.query(
            "SELECT toString(trace_id) AS tid, toString(end_user_id) AS uid "
            "FROM spans FINAL "
            "WHERE trace_id IN %(tids)s AND is_deleted = 0 "
            "  AND end_user_id IS NOT NULL "
            "GROUP BY toString(trace_id), toString(end_user_id)",
            parameters={"tids": tuple(trace_ids)},
        ).result_rows
        out: dict[str, set[str]] = {tid: set() for tid in trace_ids}
        for tid, uid in rows:
            if uid and uid != "00000000-0000-0000-0000-000000000000":
                out[tid].add(uid)
        return out

    # ─── End-user metrics (tasks/session.py user rollups) ─────────────────────
    def aggregate_by_end_user(
        self,
        end_user_id: str,
        *,
        project_id: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> dict[str, Any]:
        """User-scoped roll-up used by session.py background tasks. Equivalent to:
            ObservationSpan.objects.filter(end_user=user[, optional ...])
                .aggregate(Sum(total_tokens), Sum(cost), Count("id"),
                           Min(start_time), Max(end_time))

        Plus distinct trace_count via COUNT(DISTINCT trace_id) in one CH pass.
        Returns zeros + None timestamps if no spans match (rather than {}).
        """
        where = ["is_deleted = 0", "end_user_id = %(uid)s"]
        params: dict[str, Any] = {"uid": end_user_id}
        if project_id:
            where.append("project_id = %(pid)s")
            params["pid"] = project_id
        if since:
            where.append("start_time >= %(since)s")
            params["since"] = since
        if until:
            where.append("start_time <  %(until)s")
            params["until"] = until
        rows = self._client.query(
            "SELECT count() AS n, "
            "uniqExact(trace_id) AS traces, "
            "uniqExact(trace_session_id) AS sessions, "
            "sum(prompt_tokens) AS pt, sum(completion_tokens) AS ct, "
            "sum(total_tokens) AS tt, sum(cost) AS cost, "
            "min(start_time) AS first_seen, max(end_time) AS last_seen "
            f"FROM spans FINAL WHERE {' AND '.join(where)}",
            parameters=params,
        ).result_rows
        if not rows:
            return {
                "span_count": 0, "trace_count": 0, "session_count": 0,
                "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                "cost": 0.0, "first_seen": None, "last_seen": None,
            }
        n, traces, sessions, pt, ct, tt, c, fs, ls = rows[0]
        return {
            "span_count": int(n or 0),
            "trace_count": int(traces or 0),
            "session_count": int(sessions or 0),
            "prompt_tokens": int(pt or 0),
            "completion_tokens": int(ct or 0),
            "total_tokens": int(tt or 0),
            "cost": float(c or 0.0),
            "first_seen": fs,
            "last_seen": ls,
        }

    # ─── Time-bucketed aggregates (graphs.py / monitor.py) ───────────────────
    def time_bucket_aggregate(
        self,
        project_id: str,
        *,
        interval: str = "hour",
        since: datetime,
        until: datetime,
        observation_type: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Equivalent to:
            ObservationSpan.objects.filter(project_id=, start_time__range=...)
                .annotate(bucket=TruncHour/Day/Month("start_time"))
                .values("bucket")
                .annotate(span_count=Count, tokens=Sum, cost=Sum, latency=Avg)
                .order_by("bucket")

        `interval` ∈ {"hour", "day", "week", "month"}; mapped to the CH
        toStartOfX function. Returns one row per non-empty bucket.
        """
        # Codex wave-2 P2: align weekly bucket with the shared CH builder
        # convention (tracer/services/clickhouse/query_builders/base.py:139
        # uses `toMonday`). `toStartOfWeek` defaults to Sunday in CH 25.x;
        # inconsistent with the rest of the codebase.
        bucket_fn = {
            "hour":  "toStartOfHour",
            "day":   "toStartOfDay",
            "week":  "toMonday",
            "month": "toStartOfMonth",
        }.get(interval)
        if bucket_fn is None:
            raise ValueError(
                f"interval={interval!r} not in {{'hour','day','week','month'}}"
            )
        where = [
            "is_deleted = 0",
            "project_id = %(pid)s",
            "start_time >= %(since)s",
            "start_time <  %(until)s",
        ]
        params: dict[str, Any] = {"pid": project_id, "since": since, "until": until}
        if observation_type:
            where.append("observation_type = %(otype)s")
            params["otype"] = observation_type
        rows = self._client.query(
            f"SELECT {bucket_fn}(start_time) AS bucket, "
            "count() AS span_count, sum(total_tokens) AS tokens, "
            "sum(cost) AS cost, avg(latency_ms) AS latency_ms "
            f"FROM spans FINAL WHERE {' AND '.join(where)} "
            f"GROUP BY {bucket_fn}(start_time) "
            f"ORDER BY {bucket_fn}(start_time)",
            parameters=params,
        ).result_rows
        return [
            {
                "bucket": bucket,
                "span_count": int(n or 0),
                "tokens": int(toks or 0),
                "cost": float(c or 0.0),
                "latency_ms": float(lat or 0.0),
            }
            for (bucket, n, toks, c, lat) in rows
        ]

    # ─── Generic filtered count (eval_task.py Q-object replacement) ──────────
    def count_with_filters(
        self,
        *,
        project_id: Optional[str] = None,
        trace_ids: Optional[list[str]] = None,
        observation_type: Optional[list[str] | str] = None,
        session_id: Optional[str] = None,
        created_at_gte: Optional[datetime] = None,
        created_at_range: Optional[tuple[datetime, datetime]] = None,
    ) -> int:
        """Replaces ObservationSpan.objects.filter(<Q-object>).count() for
        the specific filter set produced by parsing_evaltask_filters().

        Equivalent to building a Q with those kwargs and counting. NOT
        a general-purpose Q→CH translator; intentionally narrow to the
        eval-task filter shape so behavior is testable in isolation.

        Codex wave-2 fixes (2026-05-26):
          • P1: created_at_* predicates target the CH `created_at` column
            (schema 002 has it — earlier impl wrongly mapped to start_time
            which broke eval-task rerun sampling parity with the PG path).
          • P2: trace_ids=[] now correctly returns 0 (an empty IN-list is
            "match nothing" per the Django Q semantic; the previous code
            silently dropped the filter, overcounting).
        """
        where = ["is_deleted = 0"]
        params: dict[str, Any] = {}
        if project_id:
            where.append("project_id = %(pid)s")
            params["pid"] = project_id
        if trace_ids is not None:
            # Explicit empty-list = match nothing (matches Django
            # .filter(trace_id__in=[]).count() == 0).
            if len(trace_ids) == 0:
                return 0
            where.append("trace_id IN %(tids)s")
            params["tids"] = tuple(trace_ids)
        if observation_type:
            if isinstance(observation_type, (list, tuple, set)):
                if len(observation_type) == 0:
                    return 0
                where.append("observation_type IN %(otypes)s")
                params["otypes"] = tuple(observation_type)
            else:
                where.append("observation_type = %(otype)s")
                params["otype"] = observation_type
        if session_id:
            where.append("trace_session_id = %(sid)s")
            params["sid"] = session_id
        if created_at_gte:
            # CH v2 spans table has a real `created_at` column (schema
            # 002_spans_v2.sql); use it directly for parity with the PG
            # Q-object path.
            where.append("created_at >= %(cag)s")
            params["cag"] = created_at_gte
        if created_at_range:
            where.append("created_at BETWEEN %(cr_s)s AND %(cr_e)s")
            params["cr_s"], params["cr_e"] = created_at_range
        rows = self._client.query(
            "SELECT count() FROM spans FINAL " f"WHERE {' AND '.join(where)}",
            parameters=params,
        ).result_rows
        return int(rows[0][0]) if rows else 0

    # ─── Group-by name with aggregates (error_analysis tool patterns) ────────
    def per_project_group_by_name(
        self,
        project_id: str,
        *,
        observation_type: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        status_filter: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Equivalent to:
            ObservationSpan.objects.filter(project_id=[, observation_type=]
                                            [, start_time__gte=since])
                .values("name").annotate(usage_count=Count("id"),
                                          error_count=Count("id", filter=Q(status="error")),
                                          avg_latency=Avg("latency_ms"),
                                          total_cost=Sum("cost"))
                .order_by("-usage_count")[:limit]

        Used by AI-tool patterns (tool-usage analysis, retrieval patterns).
        `status_filter` ANDs into the WHERE (e.g. include only error rows).
        """
        where = ["is_deleted = 0", "project_id = %(pid)s"]
        params: dict[str, Any] = {"pid": project_id}
        if observation_type:
            where.append("observation_type = %(otype)s")
            params["otype"] = observation_type
        if since:
            where.append("start_time >= %(since)s")
            params["since"] = since
        if until:
            where.append("start_time <  %(until)s")
            params["until"] = until
        if status_filter:
            where.append("status = %(stat)s")
            params["stat"] = status_filter
        rows = self._client.query(
            "SELECT name, count() AS usage_count, "
            "countIf(lower(status) = 'error') AS error_count, "
            "avg(latency_ms) AS avg_latency, sum(cost) AS total_cost "
            f"FROM spans FINAL WHERE {' AND '.join(where)} "
            "GROUP BY name "
            "ORDER BY count() DESC "
            f"LIMIT {int(limit)}",
            parameters=params,
        ).result_rows
        return [
            {
                "name": name,
                "usage_count": int(usage or 0),
                "error_count": int(errs or 0),
                "avg_latency": float(lat or 0.0),
                "total_cost": float(cost or 0.0),
            }
            for (name, usage, errs, lat, cost) in rows
        ]

    # ─── Parsing eval-task filters for CH (companion to PG Q-object) ─────────
    @staticmethod
    def parsing_evaltask_filters_for_ch(filters: dict) -> dict[str, Any]:
        """Companion to tracer/utils/eval_tasks.py::parsing_evaltask_filters.
        Same input shape — produces kwargs for count_with_filters() instead
        of a Django Q object.

        Returns a dict of kwargs ready for `**` into count_with_filters
        (or any other CH method that accepts the same subset).

        Limits: span_attributes_filters are NOT translated here (they need
        the FilterEngine v2 path). Caller should fall back to the v2 query
        builder for that subset.
        """
        out: dict[str, Any] = {}
        if not filters:
            return out
        if (otype := filters.get("observation_type")):
            out["observation_type"] = otype
        if (sid := filters.get("session_id")):
            out["session_id"] = str(sid)
        if (dr := filters.get("date_range")):
            if isinstance(dr, (list, tuple)) and len(dr) == 2:
                out["created_at_range"] = (dr[0], dr[1])
        if (cag := filters.get("created_at")):
            out["created_at_gte"] = cag
        if (pid := filters.get("project_id")):
            out["project_id"] = str(pid)
        # `trace_ids` derived from `session_id` (Trace lookup) is the
        # caller's responsibility; this helper stays narrow.
        return out

    # ─── Wave-3 reader extensions (commit 2f7d55e14 follow-up) ────────────────

    def time_bucket_aggregate_with_filters(
        self,
        *,
        interval: str,
        since: datetime,
        until: datetime,
        project_id: Optional[str] = None,
        trace_ids: Optional[list[str]] = None,
        observation_type: Optional[list[str] | str] = None,
        session_id: Optional[str] = None,
        status_filter: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Bucketed aggregation with the full eval-task filter shape PLUS
        error_count. Equivalent to:
            .filter(<filters>, start_time__range=(since, until))
                .annotate(bucket=TruncHour/Day/Week/Month("start_time"))
                .values("bucket")
                .annotate(span_count=Count, error_count=Count(filter=Q(status="error")),
                          tokens=Sum, cost=Sum, latency_ms=Avg)
                .order_by("bucket")

        Used by monitor.py / monitor_graphs.py time-window metrics where
        a stratified status filter is needed (ERROR_FREE_SESSION_RATES,
        per-provider error rates).
        """
        bucket_fn = {
            "hour":  "toStartOfHour", "day":   "toStartOfDay",
            "week":  "toMonday",      "month": "toStartOfMonth",
        }.get(interval)
        if bucket_fn is None:
            raise ValueError(
                f"interval={interval!r} not in {{'hour','day','week','month'}}"
            )
        where = ["is_deleted = 0", "start_time >= %(since)s", "start_time <  %(until)s"]
        params: dict[str, Any] = {"since": since, "until": until}
        if project_id:
            where.append("project_id = %(pid)s")
            params["pid"] = project_id
        if trace_ids is not None:
            if len(trace_ids) == 0:
                return []
            where.append("trace_id IN %(tids)s")
            params["tids"] = tuple(trace_ids)
        if observation_type:
            if isinstance(observation_type, (list, tuple, set)):
                where.append("observation_type IN %(otypes)s")
                params["otypes"] = tuple(observation_type)
            else:
                where.append("observation_type = %(otype)s")
                params["otype"] = observation_type
        if session_id:
            where.append("trace_session_id = %(sid)s")
            params["sid"] = session_id
        if status_filter:
            where.append("status = %(stat)s")
            params["stat"] = status_filter
        rows = self._client.query(
            f"SELECT {bucket_fn}(start_time) AS bucket, "
            "count() AS span_count, "
            "countIf(lower(status) = 'error') AS error_count, "
            "sum(total_tokens) AS tokens, sum(cost) AS cost, "
            "avg(latency_ms) AS latency_ms "
            f"FROM spans FINAL WHERE {' AND '.join(where)} "
            f"GROUP BY {bucket_fn}(start_time) "
            f"ORDER BY {bucket_fn}(start_time)",
            parameters=params,
        ).result_rows
        return [
            {
                "bucket": bucket,
                "span_count": int(n or 0),
                "error_count": int(errs or 0),
                "tokens": int(toks or 0),
                "cost": float(c or 0.0),
                "latency_ms": float(lat or 0.0),
            }
            for (bucket, n, errs, toks, c, lat) in rows
        ]

    def aggregate_window_with_filters(
        self,
        *,
        since: datetime,
        until: datetime,
        project_id: Optional[str] = None,
        trace_ids: Optional[list[str]] = None,
        observation_type: Optional[list[str] | str] = None,
        session_id: Optional[str] = None,
        status_filter: Optional[str] = None,
    ) -> dict[str, Any]:
        """Single-bucket variant of time_bucket_aggregate_with_filters. Used
        by monitor._get_metric_value paths that want one window-wide number.
        """
        where = ["is_deleted = 0", "start_time >= %(since)s", "start_time <  %(until)s"]
        params: dict[str, Any] = {"since": since, "until": until}
        if project_id:
            where.append("project_id = %(pid)s")
            params["pid"] = project_id
        if trace_ids is not None:
            if len(trace_ids) == 0:
                return {"span_count": 0, "error_count": 0, "tokens": 0,
                        "cost": 0.0, "latency_ms": 0.0}
            where.append("trace_id IN %(tids)s")
            params["tids"] = tuple(trace_ids)
        if observation_type:
            if isinstance(observation_type, (list, tuple, set)):
                where.append("observation_type IN %(otypes)s")
                params["otypes"] = tuple(observation_type)
            else:
                where.append("observation_type = %(otype)s")
                params["otype"] = observation_type
        if session_id:
            where.append("trace_session_id = %(sid)s")
            params["sid"] = session_id
        if status_filter:
            where.append("status = %(stat)s")
            params["stat"] = status_filter
        rows = self._client.query(
            "SELECT count() AS n, countIf(lower(status) = 'error') AS errs, "
            "sum(total_tokens) AS toks, sum(cost) AS cost, "
            "avg(latency_ms) AS lat "
            f"FROM spans FINAL WHERE {' AND '.join(where)}",
            parameters=params,
        ).result_rows
        n, errs, toks, c, lat = rows[0] if rows else (0, 0, 0, 0.0, 0.0)
        return {
            "span_count": int(n or 0),
            "error_count": int(errs or 0),
            "tokens": int(toks or 0),
            "cost": float(c or 0.0),
            "latency_ms": float(lat or 0.0),
        }

    def list_root_spans_by_trace_ids(
        self,
        trace_ids: list[str],
        *,
        observation_type: Optional[str] = None,
    ) -> dict[str, CHSpan]:
        """For each trace_id, return the root span (parent_span_id = '').
        Picks the earliest by (start_time, id) on ties. Returns a dict so
        callers can do O(1) trace_id → root_span lookups without zipping
        result orders to input orders.

        Replaces patterns like:
            ObservationSpan.objects.filter(trace_id__in=, parent_span_id="",
                                            deleted=False)
                .order_by("trace_id", "start_time").distinct("trace_id")

        Used by model_hub/services/bulk_selection.py for per-trace root
        lookups + annotation_queues.py default-queue resolution.
        """
        if not trace_ids:
            return {}
        where = [
            "is_deleted = 0", "trace_id IN %(tids)s", "parent_span_id = ''",
        ]
        params: dict[str, Any] = {"tids": tuple(trace_ids)}
        if observation_type:
            where.append("observation_type = %(otype)s")
            params["otype"] = observation_type
        # Single CH query; ORDER BY trace_id, start_time, id; then dedupe by
        # trace_id in Python keeping the first per trace_id (= earliest).
        rows = self._client.query(
            f"SELECT {_SELECT_SQL} FROM spans FINAL "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY trace_id, start_time, id",
            parameters=params,
        ).result_rows
        result: dict[str, CHSpan] = {}
        for r in rows:
            span = _row_to_chspan(r)
            if span.trace_id not in result:
                result[span.trace_id] = span
        return result

    def aggregate_by_session_ids(
        self,
        session_ids: list[str],
        *,
        project_id: Optional[str] = None,
    ) -> dict[str, dict[str, Any]]:
        """Per-session rollup across many sessions in one CH query.

        Replaces .filter(trace__session_id__in=session_ids)
                  .values("trace__session_id")
                  .annotate(Count, Sum(tokens), Sum(cost), distinct trace_count,
                            Min(start_time), Max(end_time))
        """
        if not session_ids:
            return {}
        where = [
            "is_deleted = 0", "trace_session_id IN %(sids)s",
        ]
        params: dict[str, Any] = {"sids": tuple(session_ids)}
        if project_id:
            where.append("project_id = %(pid)s")
            params["pid"] = project_id
        rows = self._client.query(
            "SELECT toString(trace_session_id) AS sid, "
            "count() AS span_count, "
            "uniqExact(trace_id) AS traces_count, "
            "sum(total_tokens) AS tokens, sum(cost) AS cost, "
            "min(start_time) AS start_time, max(end_time) AS end_time "
            f"FROM spans FINAL WHERE {' AND '.join(where)} "
            "GROUP BY toString(trace_session_id)",
            parameters=params,
        ).result_rows
        return {
            sid: {
                "span_count": int(sc or 0),
                "traces_count": int(tc or 0),
                "tokens": int(toks or 0),
                "cost": float(c or 0.0),
                "start_time": st,
                "end_time": et,
            }
            for (sid, sc, tc, toks, c, st, et) in rows
        }

    def has_root_spans_of_type(
        self, project_id: str, observation_type: str
    ) -> bool:
        """Equivalent to:
            ObservationSpan.objects.filter(project_id=, observation_type=,
                                            parent_span_id__isnull=True,
                                            deleted=False).exists()

        Used for has_voice_traces / has_<type>_traces gate checks.
        """
        rows = self._client.query(
            "SELECT 1 FROM spans FINAL "
            "WHERE is_deleted = 0 AND project_id = %(pid)s "
            "  AND observation_type = %(otype)s "
            "  AND parent_span_id = '' "
            "LIMIT 1",
            parameters={"pid": project_id, "otype": observation_type},
        ).result_rows
        return bool(rows)

    def per_project_version_metric_aggregate(
        self,
        project_version_ids: list[str],
        *,
        observation_type: Optional[str] = None,
    ) -> dict[str, dict[str, Any]]:
        """Per-project-version rollups across many versions in one query.

        Replaces patterns in tracer/views/project_version.py that do:
            .filter(project_version_id__in=).values("project_version_id")
            .annotate(avg_latency_root=Avg(...), avg_cost=Avg(...),
                      total_tokens=Sum(...), span_count=Count(...))

        For root-only metrics (e.g. average root-span latency), pass
        observation_type=None and the caller filters parent_span_id='';
        we expose both `avg_latency_root` (parent_span_id='') and overall
        `avg_latency` so callers pick which they need.
        """
        if not project_version_ids:
            return {}
        where = ["is_deleted = 0", "project_version_id IN %(pvids)s"]
        params: dict[str, Any] = {"pvids": tuple(project_version_ids)}
        if observation_type:
            where.append("observation_type = %(otype)s")
            params["otype"] = observation_type
        rows = self._client.query(
            "SELECT toString(project_version_id) AS pvid, "
            "count() AS span_count, "
            "uniqExact(trace_id) AS trace_count, "
            "avgIf(latency_ms, parent_span_id = '') AS avg_latency_root, "
            "avg(latency_ms) AS avg_latency, "
            "avg(cost) AS avg_cost, "
            "sum(total_tokens) AS total_tokens, "
            "sum(cost) AS total_cost "
            f"FROM spans FINAL WHERE {' AND '.join(where)} "
            "GROUP BY toString(project_version_id)",
            parameters=params,
        ).result_rows
        return {
            pvid: {
                "span_count": int(sc or 0),
                "trace_count": int(tc or 0),
                "avg_latency_root": float(alr) if alr is not None else None,
                "avg_latency": float(al) if al is not None else None,
                "avg_cost": float(ac) if ac is not None else None,
                "total_tokens": int(tt or 0),
                "total_cost": float(tc2 or 0.0),
            }
            for (pvid, sc, tc, alr, al, ac, tt, tc2) in rows
        }

    def trace_aggregate_with_stddev(
        self,
        trace_ids: list[str],
        *,
        parent_only: bool = True,
    ) -> dict[str, dict[str, Any]]:
        """Per-trace aggregate including stddev(latency_ms), used by
        project_version.py outlier-detection paths. `parent_only` restricts
        to root spans (matches the legacy ORM call's
        parent_span_id__isnull=True scoping).

        Equivalent to:
            .filter(trace_id__in=[, parent_span_id__isnull=parent_only]).values("trace_id")
            .annotate(latency=Avg, latency_stddev=StdDev, cost=Sum, count=Count)
        """
        if not trace_ids:
            return {}
        where = ["is_deleted = 0", "trace_id IN %(tids)s"]
        params: dict[str, Any] = {"tids": tuple(trace_ids)}
        if parent_only:
            where.append("parent_span_id = ''")
        rows = self._client.query(
            "SELECT toString(trace_id) AS tid, "
            "count() AS span_count, "
            "avg(latency_ms) AS avg_latency_ms, "
            "stddevSamp(latency_ms) AS stddev_latency_ms, "
            "sum(cost) AS cost, sum(total_tokens) AS tokens "
            f"FROM spans FINAL WHERE {' AND '.join(where)} "
            "GROUP BY toString(trace_id)",
            parameters=params,
        ).result_rows
        return {
            tid: {
                "span_count": int(sc or 0),
                "avg_latency_ms": float(al) if al is not None else None,
                "stddev_latency_ms": float(sd) if sd is not None else None,
                "cost": float(c or 0.0),
                "tokens": int(t or 0),
            }
            for (tid, sc, al, sd, c, t) in rows
        }

    def prev_next_span_by_start_time(
        self,
        *,
        project_id: str,
        span_id: str,
        project_version_id: Optional[str] = None,
        observation_type: Optional[str] = None,
    ) -> tuple[Optional[str], Optional[str]]:
        """Return (prev_span_id, next_span_id) by start_time ordering within
        the project (and optionally project_version + observation_type) scope.
        None for boundary cases.

        Replaces the row-walking pattern in
        tracer/views/observation_span.py::get_trace_id_by_index_spans_as_*.
        """
        # First, look up the focal span's start_time + id.
        anchor_rows = self._client.query(
            "SELECT start_time, id FROM spans FINAL "
            "WHERE id = %(sid)s AND is_deleted = 0 LIMIT 1",
            parameters={"sid": span_id},
        ).result_rows
        if not anchor_rows:
            return (None, None)
        anchor_st, anchor_id = anchor_rows[0]
        where_base = ["is_deleted = 0", "project_id = %(pid)s"]
        params: dict[str, Any] = {"pid": project_id, "anchor_st": anchor_st,
                                  "anchor_id": anchor_id}
        if project_version_id:
            where_base.append("project_version_id = %(pvid)s")
            params["pvid"] = project_version_id
        if observation_type:
            where_base.append("observation_type = %(otype)s")
            params["otype"] = observation_type
        base = " AND ".join(where_base)
        prev = self._client.query(
            f"SELECT id FROM spans FINAL WHERE {base} "
            "  AND (start_time, id) < (%(anchor_st)s, %(anchor_id)s) "
            "ORDER BY start_time DESC, id DESC LIMIT 1",
            parameters=params,
        ).result_rows
        nxt = self._client.query(
            f"SELECT id FROM spans FINAL WHERE {base} "
            "  AND (start_time, id) > (%(anchor_st)s, %(anchor_id)s) "
            "ORDER BY start_time ASC, id ASC LIMIT 1",
            parameters=params,
        ).result_rows
        return (
            prev[0][0] if prev else None,
            nxt[0][0] if nxt else None,
        )

    def prev_next_trace_by_start_time(
        self,
        *,
        project_id: str,
        trace_id: str,
        project_version_id: Optional[str] = None,
    ) -> tuple[Optional[str], Optional[str]]:
        """Return (prev_trace_id, next_trace_id) ordered by the trace's
        root-span start_time. Mirrors prev_next_span_by_start_time but at
        the trace level. Used by experiment-mode trace navigation.
        """
        anchor_rows = self._client.query(
            "SELECT min(start_time) AS st FROM spans FINAL "
            "WHERE trace_id = %(tid)s AND is_deleted = 0 AND parent_span_id = ''",
            parameters={"tid": trace_id},
        ).result_rows
        if not anchor_rows or anchor_rows[0][0] is None:
            return (None, None)
        anchor_st = anchor_rows[0][0]
        where_base = [
            "is_deleted = 0", "project_id = %(pid)s", "parent_span_id = ''",
        ]
        params: dict[str, Any] = {"pid": project_id, "anchor_st": anchor_st,
                                  "anchor_tid": trace_id}
        if project_version_id:
            where_base.append("project_version_id = %(pvid)s")
            params["pvid"] = project_version_id
        base = " AND ".join(where_base)
        prev = self._client.query(
            f"SELECT toString(trace_id) FROM spans FINAL WHERE {base} "
            "  AND (start_time, trace_id) < (%(anchor_st)s, toUUID(%(anchor_tid)s)) "
            "ORDER BY start_time DESC, trace_id DESC LIMIT 1",
            parameters=params,
        ).result_rows
        nxt = self._client.query(
            f"SELECT toString(trace_id) FROM spans FINAL WHERE {base} "
            "  AND (start_time, trace_id) > (%(anchor_st)s, toUUID(%(anchor_tid)s)) "
            "ORDER BY start_time ASC, trace_id ASC LIMIT 1",
            parameters=params,
        ).result_rows
        return (
            prev[0][0] if prev else None,
            nxt[0][0] if nxt else None,
        )

    # ─── Aggregations ────────────────────────────────────────────────────────
    def trace_aggregate(self, trace_id: str) -> dict[str, Any]:
        """Computes the same aggregate the eval runner needs for trace-level
        evals: total tokens, total cost, span count, max end_time.
        """
        rows = self._client.query(
            "SELECT count() AS span_count, "
            "sum(prompt_tokens) AS prompt_tokens, "
            "sum(completion_tokens) AS completion_tokens, "
            "sum(total_tokens) AS total_tokens, "
            "sum(cost) AS cost, "
            "max(end_time) AS last_end "
            "FROM spans FINAL WHERE trace_id = %(trace_id)s AND is_deleted = 0",
            parameters={"trace_id": trace_id},
        ).result_rows
        if not rows:
            return {}
        n, pt, ct, tt, c, last_end = rows[0]
        return {
            "span_count": int(n or 0),
            "prompt_tokens": int(pt or 0),
            "completion_tokens": int(ct or 0),
            "total_tokens": int(tt or 0),
            "cost": float(c or 0.0),
            "last_end": last_end,
        }

    # ─── Convenience: JSON-decoded input/output ──────────────────────────────
    @staticmethod
    def input_as_json(span: CHSpan) -> Any:
        return _maybe_json(span.input)

    @staticmethod
    def output_as_json(span: CHSpan) -> Any:
        return _maybe_json(span.output)

    @staticmethod
    def attributes_extra_as_dict(span: CHSpan) -> dict:
        try:
            return json.loads(span.attributes_extra) if span.attributes_extra else {}
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def to_django_dict(span: CHSpan) -> dict[str, Any]:
        """Convert a CHSpan to a dict shaped like ObservationSpanSerializer output.

        Drop-in for consumers that did:

            spans_data = ObservationSpanSerializer(qs, many=True).data

        Mapping notes (per the serializer's `fields` list in
        tracer/serializers/observation_span.py):
          • FK fields (`project`, `trace`, `project_version`, `custom_eval_config`,
            `prompt_version`) are emitted as ID strings — same as the
            PrimaryKeyRelatedField serializer output.
          • Derived `provider_logo` / `span_attributes` are computed from
            the span (provider→logo URL is a static map; span_attributes is
            the merge of attrs_string/number/bool + attributes_extra).
          • Fields that exist in the Django model but NOT in the CH spans
            table (`model_parameters`, `response_time`, `eval_id`,
            `org_user_id`) emit None. The consumer either doesn't read them
            (most cases — frontend just renders what's present) or will
            need the CH schema to add them in a future migration.
        """
        try:
            metadata_parsed = json.loads(span.metadata) if span.metadata else {}
        except json.JSONDecodeError:
            metadata_parsed = {}
        # span_attributes is the legacy serializer field that flattens
        # attrs_string/number/bool + attributes_extra into one dict — the
        # shape v1 consumers expect.
        span_attributes: dict[str, Any] = {}
        span_attributes.update(span.attrs_string or {})
        span_attributes.update(span.attrs_number or {})
        span_attributes.update(span.attrs_bool or {})
        try:
            extra = json.loads(span.attributes_extra) if span.attributes_extra else {}
            if isinstance(extra, dict):
                span_attributes.update(extra)
        except json.JSONDecodeError:
            pass
        # tags / span_events come from CH as JSON strings; the serializer
        # returns them as Python objects.
        try:
            tags_parsed = json.loads(span.tags) if span.tags else []
        except json.JSONDecodeError:
            tags_parsed = []
        try:
            span_events_parsed = json.loads(span.span_events) if span.span_events else []
        except json.JSONDecodeError:
            span_events_parsed = []
        return {
            "id": span.id,
            "project": span.project_id,
            "project_version": span.project_version_id,
            "trace": span.trace_id,
            "parent_span_id": span.parent_span_id or None,
            "name": span.name,
            "observation_type": span.observation_type,
            "start_time": span.start_time.isoformat() if span.start_time else None,
            "end_time": span.end_time.isoformat() if span.end_time else None,
            "input": _maybe_json(span.input),
            "output": _maybe_json(span.output),
            "model": span.model,
            "model_parameters": None,                                   # not on CH spans yet — see docstring
            "latency_ms": span.latency_ms,
            "org_id": span.org_id,
            "org_user_id": None,                                        # not on CH spans yet
            "prompt_tokens": span.prompt_tokens,
            "completion_tokens": span.completion_tokens,
            "total_tokens": span.total_tokens,
            "response_time": None,                                      # not on CH spans yet
            "eval_id": None,                                            # not on CH spans yet
            "cost": span.cost,
            "status": span.status,
            "status_message": span.status_message,
            "tags": tags_parsed,
            "metadata": metadata_parsed,
            "span_events": span_events_parsed,
            "provider": span.provider,
            "provider_logo": _provider_logo_url(span.provider),
            "span_attributes": span_attributes,
            "custom_eval_config": span.custom_eval_config_id,
            "eval_status": span.eval_status,
            "prompt_version": span.prompt_version_id,
        }


# Provider → logo URL map. Mirrors what the serializer's get_provider_logo() does
# in tracer/serializers/observation_span.py. Cached as a module constant.
_PROVIDER_LOGOS: dict[str, str] = {
    "openai": "https://app.futureagi.com/static/providers/openai.svg",
    "anthropic": "https://app.futureagi.com/static/providers/anthropic.svg",
    "google": "https://app.futureagi.com/static/providers/google.svg",
    "gcp.vertex.agent": "https://app.futureagi.com/static/providers/google.svg",
    "vapi": "https://app.futureagi.com/static/providers/vapi.svg",
    "retell": "https://app.futureagi.com/static/providers/retell.svg",
}


def _provider_logo_url(provider: str | None) -> str | None:
    """Return the provider logo URL, or None if the provider isn't mapped.

    Kept simple — the serializer has more elaborate fallback logic but
    most callers just render the URL or fall back to a generic icon.
    """
    if not provider:
        return None
    return _PROVIDER_LOGOS.get(provider.lower())


def _maybe_json(s: str) -> Any:
    if not s:
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return s
