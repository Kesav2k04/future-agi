"""Test helpers for seeding the CH 25.3 ``spans`` table directly.

Tests that exercise CH-backed endpoints used to seed PG via
``ObservationSpan.objects.create(...)`` and rely on PeerDB CDC to propagate
the row to CH. There's no CDC in the test path post-CH25-cutover, so the
endpoint reads return empty even when PG is populated.

This module gives tests an explicit, CH-direct seed function: one call per
ObservationSpan instance and the same row lands in the CH ``spans`` table
the reader queries. No "magic" signals — tests opt in when they need CH
coverage.

Typical usage::

    from tracer.tests._ch_seed import seed_ch_span

    span = ObservationSpan.objects.create(...)
    seed_ch_span(span)                    # ← one new line
    response = auth_client.get("/some/ch-backed/endpoint/")

Or seed many at once via ``seed_ch_spans([span1, span2, ...])``.

The helper goes through the same ``adapt()`` path the production
PG→CH backfill uses, so test rows have the same shape as real spans.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from tracer.services.clickhouse.v2 import get_v2_config
from tracer.services.clickhouse.v2.adapter import (
    CH_INSERT_COLUMNS,
    adapt,
    row_to_tuple,
)


def _pg_row_from_django_span(span: Any) -> dict[str, Any]:
    """Project a Django ``ObservationSpan`` instance into the dict shape
    ``adapter.adapt()`` expects. Mirrors what the PG→CH backfill reads.
    """
    project_id = getattr(span, "project_id", None)
    org_id = None
    project = getattr(span, "project", None)
    if project is not None:
        org_id = getattr(project, "organization_id", None)

    return {
        "id": str(span.id),
        "trace_id": str(getattr(span, "trace_id", "") or ""),
        "project_id": str(project_id) if project_id else None,
        "project_version_id": getattr(span, "project_version_id", None),
        "org_id": str(org_id) if org_id else None,
        "parent_span_id": getattr(span, "parent_span_id", None),
        "name": getattr(span, "name", None) or "",
        "observation_type": getattr(span, "observation_type", None) or "unknown",
        "operation_name": getattr(span, "operation_name", None),
        "status": getattr(span, "status", None),
        "status_message": getattr(span, "status_message", None),
        "start_time": getattr(span, "start_time", None),
        "end_time": getattr(span, "end_time", None),
        "latency_ms": getattr(span, "latency_ms", None),
        "model": getattr(span, "model", None),
        "provider": getattr(span, "provider", None),
        "prompt_tokens": getattr(span, "prompt_tokens", None),
        "completion_tokens": getattr(span, "completion_tokens", None),
        "total_tokens": getattr(span, "total_tokens", None),
        "cost": getattr(span, "cost", None),
        "input": getattr(span, "input", None),
        "output": getattr(span, "output", None),
        "span_attributes": getattr(span, "span_attributes", None) or {},
        "resource_attributes": getattr(span, "resource_attributes", None) or {},
        "metadata": getattr(span, "metadata", None) or {},
        "tags": getattr(span, "tags", None) or [],
        "span_events": getattr(span, "span_events", None) or [],
        "end_user_id": getattr(span, "end_user_id", None),
        "trace_session_id": getattr(span, "trace_session_id", None),
        "prompt_version_id": getattr(span, "prompt_version_id", None),
        "prompt_label_id": getattr(span, "prompt_label_id", None),
        "custom_eval_config_id": getattr(span, "custom_eval_config_id", None),
        "semconv_source": getattr(span, "semconv_source", None),
        "model_parameters": getattr(span, "model_parameters", None) or {},
        "input_images": getattr(span, "input_images", None) or [],
        "eval_input": getattr(span, "eval_input", None) or {},
        "eval_attributes": getattr(span, "eval_attributes", None) or {},
        "eval_status": getattr(span, "eval_status", None),
        "service_name": getattr(span, "service_name", None) or "",
        "gen_ai_system": getattr(span, "gen_ai_system", None),
        "gen_ai_operation": getattr(span, "gen_ai_operation", None),
        "input_gcs_url": getattr(span, "input_gcs_url", None),
        "output_gcs_url": getattr(span, "output_gcs_url", None),
        "created_at": getattr(span, "created_at", None),
        "updated_at": getattr(span, "updated_at", None),
        "deleted": getattr(span, "deleted", False),
    }


def _get_ch_client():
    """Lazy clickhouse-connect client bound to the v2 (test or prod) cluster."""
    import clickhouse_connect

    cfg = get_v2_config()
    return clickhouse_connect.get_client(
        host=cfg["host"],
        port=cfg["http_port"],
        username=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
    )


def seed_ch_span(span_or_dict: Any, *, client: Optional[Any] = None) -> None:
    """Insert ONE ObservationSpan into the CH ``spans`` table.

    Accepts either a Django ``ObservationSpan`` instance or a dict already
    matching the ``adapter.adapt()`` input shape. Caller-supplied ``client``
    is optional; we open a fresh one if omitted (cheap for one-off seeds).
    """
    seed_ch_spans([span_or_dict], client=client)


def seed_ch_spans(
    spans: Iterable[Any],
    *,
    client: Optional[Any] = None,
) -> int:
    """Bulk-insert ObservationSpan rows into the CH ``spans`` table.

    Returns the number of rows inserted. Uses ``adapt()`` so the row shape
    matches the production PG→CH backfill exactly (same typed-Map split,
    same attributes-extra merge, same JSON serialisation).
    """
    rows: list[tuple] = []
    for s in spans:
        pg_row = s if isinstance(s, dict) else _pg_row_from_django_span(s)
        ch_row = adapt(pg_row)
        rows.append(row_to_tuple(ch_row))

    if not rows:
        return 0

    own_client = client is None
    if own_client:
        client = _get_ch_client()
    try:
        client.insert("spans", rows, column_names=list(CH_INSERT_COLUMNS))
    finally:
        if own_client:
            client.close()

    return len(rows)


def truncate_ch_spans() -> None:
    """Wipe the CH ``spans`` table — call between tests that share fixtures.

    Cheap on a single-node test CH (sub-100ms for a few thousand rows).
    Idempotent; no-op if the table doesn't exist (e.g. before schema apply).
    """
    client = _get_ch_client()
    try:
        client.command("TRUNCATE TABLE IF EXISTS spans")
    finally:
        client.close()
