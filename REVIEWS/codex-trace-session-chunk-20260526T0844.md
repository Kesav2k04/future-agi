# Codex Review — trace_session chunk (commit 9d2747048)

**Commit reviewed:** `9d2747048` — `refactor(ch25): migrate custom_eval_config.run_evaluation ORM → CHSpanReader`

**Codex run:** `codex exec --skip-git-repo-check --sandbox read-only` (gpt-5.5, xhigh reasoning), 2026-05-26.

## Findings

### P1: Unconditional CH read in run_evaluation dispatcher
- **Location:** `futureagi/tracer/views/custom_eval_config.py:340`
- **Codex claim:** Bypasses the `EVAL_SPAN_READ_SOURCE` opt-in PG default + fallback pattern in `tfc/settings/settings.py:762` and `tracer/services/clickhouse/v2/eval_loader.py:98`. A CH outage or missing CH row produces 500 or "No observation spans found"; the original ORM query would still find PG spans.
- **Assessment (orchestrator should decide):** `EVAL_SPAN_READ_SOURCE` controls the **eval RUNNER** (`tracer/utils/eval.py`), not the dispatcher view this commit modifies. The runner reads span data while evaluating it; the view dispatches Celery tasks to evaluate spans. Two different code paths.
- D-027 explicitly removed CH→PG silent fallbacks from read paths post-migration. Prior chunk commits on this branch (`8b1db96e2`, `a2ddafc2f`, `ac0386a0f`) all use unconditional `get_reader()` with no PG fallback for non-eval-runner views. This commit follows that established pattern.
- If the orchestrator wants the dispatcher to gate on `EVAL_SPAN_READ_SOURCE` as well, that's a broader design call applying to all CH-migrated views, not just this one.

### P2: span_ids materialization for soft-delete update
- **Location:** `futureagi/tracer/views/custom_eval_config.py:354,362`
- **Codex claim:** `observation_span_id__in=span_ids` materializes every span id in Python before the soft-delete UPDATE. The original FK traversal was a DB-side queryset/subquery; large project versions could now hit memory or PG bind-param limits.
- **Assessment:** Real concern at scale. The original `observation_span__in=<observation_spans_queryset>` *could* have been resolved by PG as a JOIN/subquery, avoiding round-trip. The new path forces a CH→Python→PG hop. Practical impact depends on real project_version sizes — typically tens to low hundreds of spans per version. If a project ever has 100k+ spans for a single (project_version, observation_type), the `__in` clause would need batching.
- Mitigation if needed: batch `span_ids` into chunks (e.g. 10000) for the EvalLogger update. Out of scope for this commit.

### P3: Ordering changed from -start_time to start_time, id
- **Location:** Reader at `futureagi/tracer/services/clickhouse/v2/span_reader.py:367` returns ascending; `ObservationSpan.Meta.ordering = ["-start_time"]` at `tracer/models/observation_span.py:228`.
- **Codex claim:** Order differs.
- **Assessment:** Low-risk. The dispatcher only enqueues Celery tasks; the order spans get evaluated isn't user-facing. Note in commit body already covered this.

### P3: Existing test doesn't surgically validate the migration
- **Location:** `futureagi/tracer/tests/test_custom_eval_config.py:283`
- **Codex claim:** The test accepts 200 OR 400, passes ignored `span_ids`, doesn't assert CH was queried or tasks were dispatched.
- **Assessment:** True but out of scope for this commit. Per task instructions: "if [tests] don't [exist], do not invent ones." The test was pre-existing and loose. Tightening would be a separate test commit.

### No P0 findings
Codex explicitly says: "Org scoping looks preserved through the org-scoped `ProjectVersion` fetch plus project/project_version filters, and I did not see broken imports or new `CHSpan` attribute-access bugs in this commit. Static parse and `git diff --check` passed."

## Verdict
No blocking issues. P1 is a misclassification (codex conflated dispatcher with runner). P2 is a real but bounded concern. P3s are documented or out of scope.
