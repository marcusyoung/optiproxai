---
id: TASK-4
title: Remove smart-proxy context compaction throughout
status: Done
assignee: []
created_date: '2026-08-18 12:05'
updated_date: '2026-08-18 12:22'
labels: []
dependencies: []
priority: high
type: chore
ordinal: 8000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The smart-proxy context compaction feature (Phase A sync + Phase B background precompaction) has never been used and adds significant complexity to the proxy, config, dashboard, and docs. Remove it entirely: delete compaction.py and compaction_store.py, remove all compaction integration from proxy.py, remove compaction config models from config.py, remove compaction metrics from dashboard.py (DB columns, SQL queries, HTML rendering), delete test_compaction.py, remove compaction references from all other tests, remove compaction documentation from README.md and CONTRIBUTING.md, remove compaction config from config.example.yaml.

The _estimate_tokens() function in compaction.py is imported by router.py and proxy.py and must be relocated to a shared module before compaction.py is deleted. It depends on tiktoken, which remains a dependency.

Also add or update README documentation explaining the current input-limit routing behavior: per-model max_input_tokens in config, the router filters candidates by estimated prompt size, escalates to higher tiers when no model in the scored tier can accept the prompt, and returns HTTP 400 (input_limit_not_satisfied) if no tier can accept it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 compaction.py and compaction_store.py are deleted from src/optiproxai/
- [x] #2 _estimate_tokens() function is relocated to a shared module (or router.py) and still imported successfully by router.py and proxy.py
- [x] #3 ContextCompactionConfig, SyncCompactionConfig, BackgroundPrecompactionConfig, and SessionConfig classes are removed from config.py; context_compaction field is removed from SmartProxyConfig
- [x] #4 All compaction imports, calls, and result threading are removed from proxy.py (including _resolve_compaction, _compaction_headers, _build_compaction_worker, _reload_compaction_worker, compaction_result parameters, compaction headers in responses, and compaction worker lifecycle in startup/reload)
- [x] #5 Compaction metrics (compaction_mode, compaction_tokens_saved, compaction_original_tokens, compaction_session_id) are removed from dashboard.py: DB column creation, INSERT/VALUES in log_execution_event, SQL aggregates in _window_summary and _daily_trends, and HTML rendering in cards and daily table
- [x] #6 tests/test_compaction.py is deleted
- [x] #7 Compaction references are removed from test_dashboard.py, test_proxy_reload.py (including TestCompactionWorkerReload class), and test_api_keys_proxy.py
- [x] #8 Compaction section is removed from README.md; the one-line mention of compaction in the config example reference is removed
- [x] #9 Compaction sections, file tree entries, header lists, and checklist items are removed from CONTRIBUTING.md
- [x] #10 Compaction config block and session header cross-reference are removed from config.example.yaml
- [x] #11 README.md includes a section explaining input-limit routing behavior: per-model max_input_tokens, candidate filtering, tier escalation, and 400 error when no tier can accept the prompt
- [x] #12 uv run ruff check src/ passes
- [x] #13 uv run ruff format --check src/ tests/ passes
- [x] #14 uv run pyright src/ passes
- [x] #15 uv run pytest tests/ -q passes with no compaction-related failures
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
# Implementation Plan: Remove Smart-Proxy Context Compaction

## Rationale

The compaction feature (Phase A sync + Phase B background precompaction) has never been enabled or used. It adds ~860 lines across compaction.py + compaction_store.py, plus deep integration in proxy.py (~90 references), dashboard.py (~36 references), config.py (~12 references), and significant test/doc surface area. Removing it simplifies the proxy, config, dashboard, and documentation. The router's input-limit filtering and tier escalation already handle oversized prompts without information loss.

## Key Dependency: `_estimate_tokens`

The function `_estimate_tokens()` in compaction.py (lines 67-81) is imported by:
- `router.py` line 17 — used at line 316 for prompt token estimation during routing
- `proxy.py` line 1527 — used only inside `_resolve_compaction()` (which will be deleted)

After removing compaction from proxy.py, only router.py needs `_estimate_tokens`. The function and its helpers (`_get_encoder`, `_CHARS_PER_TOKEN`, `_encoder_cache`) will be relocated to a new module `src/optiproxai/tokens.py`. Router.py will import from there. tiktoken remains a dependency.

## Approach (Sequential)

### Step 1: Create `tokens.py` and update router.py import
- Create `src/optiproxai/tokens.py` with `_get_encoder()`, `_estimate_tokens()`, `_CHARS_PER_TOKEN`, `_encoder_cache` (moved verbatim from compaction.py lines 36-81)
- Update `router.py` line 17: `from optiproxai.compaction import _estimate_tokens` → `from optiproxai.tokens import _estimate_tokens`
- Update `router.py` `resolve_model()` docstring (line 517) that mentions compaction — change to reference internal model resolution only

### Step 2: Remove compaction from proxy.py
- Remove compaction imports (lines 25-37): `BackgroundCompactionWorker`, `CompactionResult`, `_merge_summaries`, `generate_summary`, `get_worker`, `set_worker`, `try_sync_compaction`, and compaction_store imports
- Remove `_build_compaction_worker()` (lines 198-208)
- Remove compaction worker startup in `configure()` / lifespan (lines 239-251)
- Remove `compaction_result` parameter from `_log_token_usage()` (lines 604-655) and all compaction field threading
- Remove `compaction_result` parameter from `_proxy_upstream()` (line 667)
- Remove `compaction_result` parameter from `_try_with_fallbacks()` (line 961) and its calls to `_proxy_upstream` (lines 980, 1052)
- Remove `_compaction_headers()` (lines 1480-1489)
- Remove `_resolve_compaction()` (lines 1492-1850) — this is the largest single removal (~360 lines)
- Remove compaction call in `chat_completions()` (lines 1974-1985): the `compaction_result = await _resolve_compaction(...)` call and the `body["messages"] = compaction_result.messages` replacement
- Remove compaction headers attachment (lines 2039-2043)
- Remove `compaction_result` from `_try_with_fallbacks` call in chat_completions (line 2034)
- Remove `_reload_compaction_worker()` (lines 2128-2147)
- Remove compaction worker reload call in admin reload handler (lines 2199-2206)
- Remove compaction fields from reload diff dict (lines 2225-2228)

### Step 3: Remove compaction config from config.py
- Remove `SyncCompactionConfig` class (lines 252-269)
- Remove `BackgroundPrecompactionConfig` class (lines 272-278)
- Remove `SessionConfig` class (lines 281-284)
- Remove `ContextCompactionConfig` class (lines 287-298)
- Remove `context_compaction` field from `SmartProxyConfig` (lines 344-346)

### Step 4: Remove compaction metrics from dashboard.py
- Remove compaction DB column creation (lines 91-99): four `_ensure_column` calls
- Remove compaction parameters from `log_execution_event()` (lines 400-403): `compaction_mode`, `compaction_tokens_saved`, `compaction_original_tokens`, `compaction_session_id`
- Remove compaction fields from JSONL record dict (lines 424-427)
- Remove compaction columns from INSERT SQL and VALUES (lines 310-337) — reduce placeholder count from 17 to 13
- Remove compaction aggregates from `_window_summary` SQL (lines 702-703) and result dict (lines 726-727)
- Remove compaction aggregates from `_daily_trends` SQL (lines 799-800) and result rows (lines 827-828)
- Remove compaction metrics from HTML window cards (lines 1073-1074)
- Remove compaction columns from daily table headers (lines 1159-1160) and row data (lines 1147-1148)
- Note: `formatCompact` on line 1466 is a D3.js formatter, NOT compaction-related — leave it

### Step 5: Delete compaction source files
- Delete `src/optiproxai/compaction.py` (537 lines)
- Delete `src/optiproxai/compaction_store.py` (320 lines)

### Step 6: Clean up tests
- Delete `tests/test_compaction.py` (entire file)
- `tests/test_dashboard.py`: Remove `test_log_execution_event_includes_compaction_fields`, `test_ingest_execution_logs_maps_compaction_fields`, `test_window_summary_includes_compaction_aggregates`, `test_daily_trends_includes_compaction_columns`, `test_render_window_cards_shows_compaction_metrics`, `test_render_daily_table_shows_compaction_columns`. Remove compaction fields from any test fixtures/helpers that set compaction_mode, compaction_tokens_saved, etc.
- `tests/test_proxy_reload.py`: Remove `TestCompactionWorkerReload` class (lines 1383-1462+). Remove `compaction_enabled` and `compaction_concurrency` parameters from `_config_text()` helper. Remove compaction-related smart_proxy_sections. Remove `fake_resolve_compaction` and its mock patching (lines 1510-1534). Remove compaction fields from reload diff assertions.
- `tests/test_api_keys_proxy.py`: Remove `compaction_result=None` parameter from all mock `_proxy_upstream` / `_try_with_fallbacks` signatures (4 locations). Remove the `_ = ... compaction_result` lines.

### Step 7: Clean up documentation
- `README.md`: Remove "Smart-proxy context compaction" section (lines 410-441). Update line 260 to remove "context compaction" from the config example reference list. Add new "Input-limit routing" section explaining: per-model `max_input_tokens` in config, the router estimates prompt tokens and filters candidates, escalates to higher tiers when no model in the scored tier can accept the prompt, and returns HTTP 400 `input_limit_not_satisfied` if no tier can accept it. Mention that opencode's `max_input` should be set to the largest model's context window since the proxy handles per-model filtering.
- `CONTRIBUTING.md`: Remove compaction.py and compaction_store.py from file tree (lines 185-186). Remove "compaction behavior" from proxy test areas list (line 354). Remove `test_compaction.py` from test commands (line 363). Remove "Smart-proxy context compaction" section (lines 387-404). Remove compaction headers from header lists (lines 424-426, 479-485). Remove "When changing compaction" checklist (lines 668-672).
- `config.example.yaml`: Remove compaction config block (lines 87-111). Remove session header cross-reference on line 47.
- `docs/index.md`: Line 70 uses "compact" in the sense of "compact model" (adjective, not the feature) — leave it.

### Step 8: Run full quality gates
- `uv run ruff check src/`
- `uv run ruff format --check src/ tests/`
- `uv run pyright src/`
- `uv run pytest tests/ -q`
- Fix any failures, re-run until all green

## Constraints and Risks

- **SQL placeholder count**: The dashboard INSERT statement uses 17 positional placeholders. Removing 4 compaction fields requires updating to 13 placeholders. Must verify the VALUES tuple matches.
- **Test count**: Current suite has ~390 tests. Removing test_compaction.py and compaction tests from test_dashboard.py/test_proxy_reload.py will reduce the count. This is expected.
- **Backlog task references**: Completed TASK-1.2 and TASK-1.4 mention compaction in their descriptions (e.g., "strip before compaction"). These are historical task records and should NOT be modified — they describe what was done at the time.
- **`_estimate_tokens` naming**: The function has a leading underscore (private convention) but is imported across modules. Preserve the name as-is to minimize diff; only the import path changes.
- **formatCompact in dashboard.py**: Line 1466 `const formatCompact = d3.format('~s')` is a D3.js number formatter, NOT related to context compaction. Must not be removed.

## Task Manifest

| # | Title | Files | Depends On | Labels | Acceptance Criterion |
|---|------|-------|-----------|--------|-----------------------|
| 1 | Create tokens.py, update router.py import | src/optiproxai/tokens.py, src/optiproxai/router.py | — | infra | _estimate_tokens imported from tokens.py, router.py uses it for prompt estimation, pyright passes |
| 2 | Remove compaction from proxy.py | src/optiproxai/proxy.py | 1 | infra | No compaction imports, functions, or result threading remain in proxy.py; proxy starts and routes without compaction |
| 3 | Remove compaction config models | src/optiproxai/config.py | 2 | infra | ContextCompactionConfig and sub-configs removed; SmartProxyConfig has no context_compaction field; config loads without compaction |
| 4 | Remove compaction metrics from dashboard.py | src/optiproxai/dashboard.py | 3 | database | No compaction DB columns, SQL queries, or HTML rendering remain in dashboard.py |
| 5 | Delete compaction source files | src/optiproxai/compaction.py, src/optiproxai/compaction_store.py | 2 | infra | Both files deleted; no remaining imports of compaction or compaction_store in src/ |
| 6 | Clean up tests | tests/test_compaction.py, tests/test_dashboard.py, tests/test_proxy_reload.py, tests/test_api_keys_proxy.py | 1,2,3,4,5 | test | test_compaction.py deleted; no compaction references remain in other test files |
| 7 | Clean up docs and add input-limit section | README.md, CONTRIBUTING.md, config.example.yaml | 6 | docs | Compaction sections removed from all docs; README has input-limit routing section explaining per-model max_input_tokens, tier escalation, and 400 error |
| 8 | Run quality gates | — | 7 | test | ruff check, ruff format, pyright, and pytest all pass with zero failures |
<!-- SECTION:PLAN:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Removed the smart-proxy context compaction feature (Phase A sync + Phase B background precompaction) throughout the codebase. Created src/optiproxai/tokens.py housing _estimate_tokens() (moved verbatim from compaction.py; tiktoken kept as a dependency) and updated router.py to import from it. Removed all compaction integration from proxy.py (~515 lines): imports, _build_compaction_worker, worker lifecycle in lifespan, compaction_result threading through _log_usage/_proxy_upstream/_try_with_fallbacks, _compaction_headers, _resolve_compaction, chat_completions body replacement + headers, _reload_compaction_worker, and reload diff fields. Removed SyncCompactionConfig/BackgroundPrecompactionConfig/SessionConfig/ContextCompactionConfig from config.py and the context_compaction field from SmartProxyConfig. Removed compaction DB columns, INSERT/VALUES placeholders (17→13), SQL aggregates in _window_summary/_daily_trends, and HTML cards/table columns from dashboard.py (kept the D3 formatCompact formatter). Deleted compaction.py (536 lines), compaction_store.py (319 lines), and tests/test_compaction.py (1615 lines). Cleaned compaction references from test_dashboard.py (6 test functions), test_proxy_reload.py (TestCompactionWorkerReload removed, backoff test kept in TestAdminReloadBackoff, in-flight snapshot test now swaps state inside _try_with_fallbacks), and test_api_keys_proxy.py (4 mock signatures). Removed compaction docs from README.md, CONTRIBUTING.md, config.example.yaml. Added an 'Input-limit routing' section to README explaining per-model max_input_tokens, candidate filtering, tier escalation, the 400 input_limit_not_satisfied response, and advice to set opencode max_input to the largest context window. Quality gates all green: ruff check, ruff format --check, pyright (0 errors), pytest 306 passed (1 pre-existing deselect, confirmed identical on main). Diff: +234/-3531 across 15 files. Commit NOT created: git commit is denied by permission rules. The backlog task file 'backlog/tasks/task-4 - Remove-smart-proxy-context-compaction-throughout.md' is staged alongside the code changes.
<!-- SECTION:FINAL_SUMMARY:END -->
