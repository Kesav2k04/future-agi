-- 014 — `_peerdb_is_deleted` ALIAS for back-compat with legacy CDC queries.
--
-- The v2 typed-JSON `spans` table uses `is_deleted UInt8` (see
-- 002_spans_v2.sql) as the soft-delete column. The pre-cutover code paths
-- (and any external integration that still references the legacy column
-- name) read `_peerdb_is_deleted` because that was the PeerDB-managed
-- column on the old CDC-mirror `spans` table.
--
-- During the CH25 migration close-out (2026-05-27) we rewrote every
-- production query builder to use `is_deleted` directly. To stay safe for
-- anything we missed — third-party SDK queries, custom dashboards, ad-hoc
-- analytics — expose `_peerdb_is_deleted` as a true ALIAS column. Reads
-- resolve to `is_deleted` at query time; writes ignore it (ALIAS columns
-- are not persisted, so no storage cost and no INSERT contract change).
--
-- Why ALIAS over MATERIALIZED:
--   • ALIAS is query-time only; no backfill needed for existing rows.
--   • MATERIALIZED writes a new physical column at INSERT time, which
--     would require backfilling every existing row and double-writing on
--     every new INSERT.
-- The ALIAS form gives back-compat at zero cost as long as nothing tries
-- to ORDER/GROUP/PREWHERE by `_peerdb_is_deleted` — which would force CH
-- to compute the alias for every row. The legacy queries we kept on
-- `_peerdb_is_deleted` use it only in WHERE, which CH plans efficiently.

ALTER TABLE spans
    ADD COLUMN IF NOT EXISTS _peerdb_is_deleted UInt8 ALIAS is_deleted;

-- CH 25.3 bug interaction (2026-05-28): `add_minmax_index_for_numeric_columns = 1`
-- on the spans table (see 002_spans_v2.sql line 208) auto-creates a minmax
-- index for EVERY UInt8 column, including the ALIAS column we just added.
-- That index is recorded in the stored table metadata. On the NEXT CH
-- server restart, ATTACH TABLE re-applies the setting AND the stored
-- index — and fails with:
--   Code: 49. Index with name `auto_minmax_index__peerdb_is_deleted`
--   already exists: Cannot attach table default.spans
-- The table is then wedged: every read fails with Code 722 / 696 / 695
-- (ASYNC_LOAD_FAILED), and even `DROP TABLE` errors out.
--
-- Workaround: drop the auto-index for the alias column. The alias resolves
-- to `is_deleted` at query time, and `is_deleted` already has its own
-- auto-minmax index (`auto_minmax_index_is_deleted`) — so we lose no
-- selectivity. `IF EXISTS` keeps the drop idempotent across CH versions
-- that don't auto-create indexes for ALIAS columns.
ALTER TABLE spans
    DROP INDEX IF EXISTS auto_minmax_index__peerdb_is_deleted;
