"""Deployment-wide usage counts read from the ClickHouse v2 ``spans`` table.

CH-25 moved trace/span storage out of Postgres (``Trace`` /
``ObservationSpan``) and into the denormalized CH ``spans`` table, written
directly by fi-collector on the OTLP ingest path. Telemetry's headline
usage metrics must read from there — the PG tables are being removed and
report 0 (or legacy-only) on any modern deployment.

These are deployment-wide aggregates (no project/workspace filter): the
self-hosted instance reports its total activity for the window.

Each function raises on CH error rather than returning 0, so the caller
(``collectors._safe_count``) can record the metric as *unknown* (null)
instead of masking a CH outage as genuine zero usage.
"""

from __future__ import annotations

from datetime import datetime

import structlog

logger = structlog.get_logger(__name__)

_QUERY_TIMEOUT_MS = 15000


def clickhouse_available() -> bool:
    """True when CH is enabled, configured, and reachable."""
    from tracer.services.clickhouse.client import is_clickhouse_enabled

    return is_clickhouse_enabled()


def _scalar(query: str, params: dict) -> int:
    """Run a single-cell aggregate query and return it as an int."""
    from tracer.services.clickhouse.client import get_clickhouse_client

    rows, _columns, _ms = get_clickhouse_client().execute_read(
        query,
        params,
        timeout_ms=_QUERY_TIMEOUT_MS,
    )
    if not rows or rows[0][0] is None:
        return 0
    return int(rows[0][0])


def count_spans(window_start: datetime, window_end: datetime) -> int:
    """Total non-deleted spans ingested in the window.

    ``spans`` is a ReplacingMergeTree: row-versions and tombstones coexist
    until a background merge collapses them, so the latest-row predicate
    (``is_deleted = 0``) is only correct under ``FINAL``. Without it a
    re-ingested or soft-deleted span is counted more than once.
    """
    return _scalar(
        """
        SELECT count()
        FROM spans FINAL
        WHERE start_time >= %(start)s
          AND start_time < %(end)s
          AND is_deleted = 0
        """,
        {"start": window_start, "end": window_end},
    )


def count_traces(window_start: datetime, window_end: datetime) -> int:
    """Distinct traces (by ``trace_id``) ingested in the window.

    ``FINAL`` collapses ReplacingMergeTree duplicates so a soft-deleted
    span's tombstone wins over its live version before ``is_deleted = 0``
    filters it out.
    """
    return _scalar(
        """
        SELECT uniqExact(trace_id)
        FROM spans FINAL
        WHERE start_time >= %(start)s
          AND start_time < %(end)s
          AND is_deleted = 0
        """,
        {"start": window_start, "end": window_end},
    )


def ingesting_project_ids(
    window_start: datetime,
    window_end: datetime,
) -> list[str]:
    """Project IDs that ingested at least one span in the window.

    Used to derive active users from real ingest activity (SDK-only users
    who never touch the UI), not just app-entity creators.
    """
    from tracer.services.clickhouse.client import get_clickhouse_client

    rows, _columns, _ms = get_clickhouse_client().execute_read(
        """
        SELECT DISTINCT project_id
        FROM spans FINAL
        WHERE start_time >= %(start)s
          AND start_time < %(end)s
          AND is_deleted = 0
        """,
        {"start": window_start, "end": window_end},
        timeout_ms=_QUERY_TIMEOUT_MS,
    )
    return [str(row[0]) for row in rows if row[0] is not None]
