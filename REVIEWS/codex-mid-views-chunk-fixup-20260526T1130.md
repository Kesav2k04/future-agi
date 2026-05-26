# Codex review — mid-views chunk fix-up commit

- Date: 2026-05-26T11:30Z
- Branch: `feat/ch25-spans-migration` (worktree `worktree-agent-a1f912093d41f9642`)
- Commit reviewed: `35675a68c` — `fix(ch25): address codex mid-views-chunk P1 findings`
- Codex run: `codex exec --skip-git-repo-check --sandbox read-only` (gpt-5.5, xhigh, 2026-05-26)

## Initial-pass findings — recap

The previous run on commits 652502943 / 83e2e6983 / 85001ef73 produced:

- P0: None.
- P1 (dataset.py): `add_to_new_dataset` CH reads ran without pre-validating trace_ids/span_ids by project+org. Foreign rows were dropped by a Python post-filter, so the user-facing tenancy held, but foreign rows ran through process memory between the CH fetch and the filter.
- P1 (eval_task.py): `_handle_edit_rerun` count path could run untenanted if the edit payload's `filters` omitted `project_id`.
- P2 (eval_task.py): the reader's `count_with_filters` maps `created_at_*` kwargs to `start_time`, but the v2 CH schema has a `created_at` column. Reader-level bug, out of scope per Rule 1.
- P3: stale `Cell`/`Row` imports in dataset.py; pre-existing.

## Fix-up actions in `35675a68c`

1. **dataset.py** — both `add_to_new_dataset` branches now pre-validate via PG with `id__in=..., project_id=project, project__organization=org` before issuing the CH read. The redundant Python `project_id` post-filter was removed.
2. **eval_task.py** — `_handle_edit_rerun` reads `eval_task.project_id` and force-injects it into both the PG fallback `Q` and the CH `ch_kwargs` before counting, regardless of payload contents.

## Codex re-review verdict (verbatim)

> **P0:** None found.
>
> **P1:** None found. Both reported P1s are closed.
>
> (1) `add_to_new_dataset` now validates the project against the caller org before CH reads, then pre-validates `trace_ids` with `project_id=project` and `project__organization=org` before `reader.list_by_trace_ids`, and does the same for `span_ids` before `reader.list_by_ids`: dataset.py:111, dataset.py:163, dataset.py:173, dataset.py:182, dataset.py:192.
>
> (2) `_handle_edit_rerun` now derives `tenant_project_id` from `eval_task.project_id`; the PG fallback ANDs it into `parsed_filters`, and the CH path overwrites/injects `ch_kwargs["project_id"]` before counting: eval_task.py:1319, eval_task.py:1322, eval_task.py:1330.
>
> **P2:** None newly introduced by `35675a68c`.
>
> **P3:** None newly introduced by `35675a68c`.
>
> I did a static review of the commit diff and adjacent reader/parser paths; I did not run the test suite. `git diff --check 35675a68c^ 35675a68c` reported no whitespace errors.

## Verdict

All blocking issues from the mid-views-chunk review are closed. The P2 `created_at` reader mapping remains as a reader-extension request in the final task report (NOT a wave-2 fix since the reader is out of scope per Rule 1).
