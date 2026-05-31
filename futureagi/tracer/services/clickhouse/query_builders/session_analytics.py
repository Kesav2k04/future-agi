"""
Session Analytics Query Builder for ClickHouse.

Provides queries for session-level and user-level aggregate metrics,
replacing heavy PG queries on ObservationSpan, Trace, and TraceSession
tables with efficient ClickHouse GROUP BY queries on the denormalized
``spans`` table.
"""

from typing import Any

from tracer.services.clickhouse.query_builders.base import NIL_UUID, BaseQueryBuilder
from tracer.services.clickhouse.v2.id_remap_sql import (
    remap_left_join,
    resolved_id_expr,
)


class SessionAnalyticsQueryBuilder(BaseQueryBuilder):
    """Build queries for session and user analytics aggregations.

    All queries operate on the ``spans`` table which denormalizes
    trace context (including ``trace_session_id`` and ``end_user_id``)
    into every span row.

    Args:
        project_id: Project UUID string.
    """

    TABLE = "spans"

    def __init__(self, project_id: str, **kwargs: Any) -> None:
        super().__init__(project_id, **kwargs)

    def build(self) -> tuple[str, dict[str, Any]]:
        """Not used directly -- call specific build_* methods instead."""
        raise NotImplementedError(
            "Use build_session_metrics_query, build_session_navigation_query, "
            "build_user_stats_query, or build_first_last_message_query instead."
        )

    # ------------------------------------------------------------------
    # Session metrics (per-session aggregates)
    # ------------------------------------------------------------------

    def build_session_metrics_query(
        self, session_ids: list[str]
    ) -> tuple[str, dict[str, Any]]:
        """Build a query returning per-session aggregate metrics.

        Args:
            session_ids: List of session ID strings to aggregate.

        Returns:
            A ``(query_string, params)`` tuple.
        """
        params = dict(self.params)
        params["session_ids"] = session_ids

        query = f"""
        SELECT
            trace_session_id,
            min(start_time) AS first_trace_time,
            max(start_time) AS last_trace_time,
            count(DISTINCT trace_id) AS trace_count,
            sum(total_tokens) AS total_tokens,
            sum(cost) AS total_cost,
            min(start_time) AS started_at,
            max(COALESCE(end_time, start_time)) AS ended_at
        FROM {self.TABLE}
        {self.project_where()}
          AND trace_session_id IN %(session_ids)s
        GROUP BY trace_session_id
        """
        return query, params

    # ------------------------------------------------------------------
    # Session navigation (all sessions with metrics)
    # ------------------------------------------------------------------

    def build_session_navigation_query(self) -> tuple[str, dict[str, Any]]:
        """Build a query returning all sessions with their metrics for navigation.

        Returns:
            A ``(query_string, params)`` tuple.
        """
        params = dict(self.params)

        query = f"""
        SELECT
            trace_session_id,
            min(start_time) AS started_at,
            max(COALESCE(end_time, start_time)) AS ended_at,
            count(DISTINCT trace_id) AS trace_count,
            sum(total_tokens) AS total_tokens,
            sum(cost) AS total_cost
        FROM {self.TABLE}
        {self.project_where()}
          AND trace_session_id != ''
          AND trace_session_id != toUUID('{NIL_UUID}')
        GROUP BY trace_session_id
        ORDER BY started_at DESC
        """
        return query, params

    # ------------------------------------------------------------------
    # User stats (per-user aggregates)
    # ------------------------------------------------------------------

    def build_user_stats_query(self, user_id: str) -> tuple[str, dict[str, Any]]:
        """Build a query returning aggregate stats for a specific user.

        Args:
            user_id: The end-user ID string.

        Returns:
            A ``(query_string, params)`` tuple.
        """
        params = dict(self.params)
        params["user_id"] = user_id

        # P3b step1.5 id-remap resolution (DESIGN §3 / id_remap_sql): `user_id`
        # here is the OLD curated id (callers pass `str(EndUser.id)` /
        # `str(user.id)` — tracer/tasks/session.py, tracer/utils/session.py). A
        # cross-cutover straddler's NEW (deterministic-id) spans carry
        # `end_user_id = new_id`, so resolve each span new→old through
        # `end_user_id_remap` and match the OLD id on the RESOLVED value, so
        # old + new spans roll into this per-user stat as ONE user. The project
        # scope predicate stays on the bare inner scan; only the identity match
        # moves to the resolved layer. `resolved_id_expr` is the zero-uuid-guarded
        # map (NOT a COALESCE — an unmatched LEFT JOIN fills `old_id` with the
        # zero-uuid; see id_remap_sql). Pre-flip NO span matches a `new_id`, so
        # the resolved id == the span's own id and this is a no-op (gate B).
        remap_join = remap_left_join("rs.end_user_id", "end_user_id_remap")
        resolved_eu = resolved_id_expr("rs.end_user_id")
        query = f"""
        SELECT
            count(DISTINCT trace_session_id) AS session_count,
            sum(total_tokens) AS total_tokens,
            sum(cost) AS total_cost,
            min(start_time) AS first_seen,
            max(start_time) AS last_seen
        FROM (
            SELECT
                {resolved_eu} AS end_user_id,
                rs.trace_session_id AS trace_session_id,
                rs.total_tokens AS total_tokens,
                rs.cost AS cost,
                rs.start_time AS start_time
            FROM (
                SELECT end_user_id, trace_session_id, total_tokens, cost, start_time
                FROM {self.TABLE}
                {self.project_where()}
            ) AS rs
            {remap_join}
        )
        WHERE end_user_id = %(user_id)s
        """
        return query, params

    # ------------------------------------------------------------------
    # First/last message per session
    # ------------------------------------------------------------------

    def build_first_last_message_query(
        self, session_ids: list[str]
    ) -> tuple[str, dict[str, Any]]:
        """Build queries returning the first and last input/output per session.

        Uses ClickHouse's ``LIMIT 1 BY`` to efficiently get the first and
        last root spans per session.

        Args:
            session_ids: List of session ID strings.

        Returns:
            A ``(first_query, last_query, params)`` tuple. Both queries share
            the same params dict.
        """
        params = dict(self.params)
        params["session_ids"] = session_ids

        first_query = f"""
        SELECT trace_session_id, input, output
        FROM {self.TABLE}
        {self.project_where()}
          AND trace_session_id IN %(session_ids)s
          AND (parent_span_id IS NULL OR parent_span_id = '')
        ORDER BY start_time ASC
        LIMIT 1 BY trace_session_id
        """

        last_query = f"""
        SELECT trace_session_id, input, output
        FROM {self.TABLE}
        {self.project_where()}
          AND trace_session_id IN %(session_ids)s
          AND (parent_span_id IS NULL OR parent_span_id = '')
        ORDER BY start_time DESC
        LIMIT 1 BY trace_session_id
        """

        return first_query, last_query, params
