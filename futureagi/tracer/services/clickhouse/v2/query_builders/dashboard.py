"""
v2 Dashboard query builder — targets the CH 25.3 spans schema.

Subclass + post-rewrite. The v1 dashboard builder emits 1 SQL query per
dashboard metric (latency, p95, model breakdown, custom-attribute pivots,
etc.). Each metric type goes through `build_metric_query()`; `build_all_queries`
fans out over it and returns `[(sql, params, meta), …]`.

Unlike the list builders, the dashboard builder dispatches EVERY metric type
through that ONE polymorphic method. A metric may target the migrated `spans`
schema (system_metric / custom_attribute) OR a non-migrated legacy table
(eval_metric → `usage_apicalllog`, annotation_metric → `model_hub_score`, both
still on `_peerdb_is_deleted` / `deleted`). `V2RewriteMixin`'s blanket auto-wrap
can't make that per-metric distinction — it would rename `_peerdb_is_deleted` →
`is_deleted` on the legacy tables, producing "Identifier 'e.is_deleted' cannot be
resolved" (the TH-5911 / TH-5964 failure mode; see DECISIONS #033). So both
dispatch methods are excluded from the mixin and the rewrite is applied here,
per metric, skipping the legacy-table types.
"""

from __future__ import annotations

from tracer.services.clickhouse.query_builders.dashboard import DashboardQueryBuilder
from tracer.services.clickhouse.v2.query_builders._rewrite import V2RewriteMixin
from tracer.services.clickhouse.v2.query_builders.filters import (
    rewrite_and_apply_v2_settings,
)


# Metric types whose SQL reads tables NOT migrated to the CH 25.3 spans schema.
# Their `_peerdb_is_deleted` / `deleted` columns must survive the v1→v2 rewrite
# untouched (PLAN_V2_NO_CDC: "existing usage_apicalllog continues").
_LEGACY_TABLE_METRIC_TYPES = frozenset({"eval_metric", "annotation_metric"})


class DashboardQueryBuilderV2(V2RewriteMixin, DashboardQueryBuilder):
    """Drop-in v2 Dashboard builder.

    The v1 builder is unusual in that it's NOT a subclass of BaseQueryBuilder —
    it owns its own composition logic for the metric → SQL mapping. We inherit
    it the same way.

    Both `build_metric_query` and `build_all_queries` are excluded from the
    mixin's blanket rewrite because they are polymorphic over metric type (see
    module docstring). `build_metric_query` applies the rewrite itself, per
    metric, skipping the legacy-table types; `build_all_queries` (inherited from
    v1) fans out over it, so its output is already correctly rewritten and must
    NOT be re-wrapped (re-wrapping would also double the v2 SETTINGS clause).

    Residual gap (accepted, == DECISIONS #033 / TH-6022): an eval/annotation
    metric whose breakdown/filter forces a JOIN onto the migrated `spans` table
    is a mixed-table query — the `s.` spans alias would need the rewrite while
    the `e.`/`a.` legacy aliases must not, and a whole-string rewrite can't tell
    them apart. Such a metric is left un-rewritten and handled gracefully by the
    view's per-metric isolation rather than failing the whole dashboard.
    """

    _v2_rewrite_exclude = frozenset({"build_metric_query", "build_all_queries"})

    def build_metric_query(self, metric: dict):
        sql, params = super().build_metric_query(metric)
        if metric.get("type") in _LEGACY_TABLE_METRIC_TYPES:
            # Legacy-table SQL — leave its columns on the v1 schema.
            return sql, params
        return rewrite_and_apply_v2_settings(sql), params


__all__ = ["DashboardQueryBuilderV2"]
