---
id: TASK-8
title: >-
  Fix Major findings from codebase review (doc-3): embedding timeout, routing
  hot-path re-init, config encoding
status: In Progress
assignee: []
created_date: '2026-08-18 14:08'
updated_date: '2026-08-18 14:36'
labels: []
dependencies: []
documentation:
  - code_reviews/doc-3 - Codebase-Review-—-main-2026-08-18.md
priority: high
type: bug
ordinal: 12000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Address the findings from the full-codebase review of main recorded in doc-3 (code_reviews/doc-3 - Codebase-Review-—-main-2026-08-18.md).

Context: the review scored the codebase healthy overall (307/307 tests, ruff and pyright clean) but flagged two Major and eight Minor findings. The three Major findings and the cheap hygiene items are bundled here; remaining Minor findings and cross-cutting observations stay in doc-3 for future consideration.

Major:
1. scorer.py _embed_text — the configured embedding timeout is ineffective: the executor context manager's shutdown(wait=True) blocks until the underlying request finishes, so a stalled embedding call can hang ~600s instead of the configured ~5s and leaks a thread per request.
2. router.py _classify — a new Scorer is constructed per routing decision, so every request re-pickles feature_classifier.pkl, re-parses config.yaml for embedding settings, and builds a new OpenAI client; in local embedding mode the sentence-transformers model is reloaded from disk per request.
3. config.py:553 / cli.py:124 — config.yaml is opened without encoding="utf-8", raising UnicodeDecodeError for non-ASCII YAML on Windows (cp1252).

Also in scope (cheap hygiene from the same review): structured error instead of raw KeyError on the pass-through default-provider path, secrets.compare_digest for the admin token, load-time validation of ambiguous_bands, single models-directory resolver shared by doctor and scorer, and dead code removal (router.py helpers, feature_training.py EMBEDDING_DIM).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 # _embed_text enforces the configured embedding timeout_seconds so a slow/stalled upstream aborts within the bound (no shutdown wait, no thread accumulation) — covered by a test simulating a stalled upstream
- [x] #2 # Router holds a persistent Scorer instance so feature_classifier.pkl is loaded at most once per process (not per routing decision) — covered by a test counting pickle loads across multiple route() calls
- [x] #3 # Runtime embedding settings are memoized per config state (re-resolved on config reload only), so config.yaml is not re-parsed per embedding request
- [x] #4 # In local embedding mode, a single LocalEmbeddingBackend instance is reused, so the sentence-transformers model loads once per process — covered by a test counting model loads
- [x] #5 # config.yaml reads in config.py and cli.py use encoding="utf-8"; a test with a non-ASCII YAML passes on Windows
- [x] #6 # Pass-through branch of /v1/chat/completions returns a structured OpenAI-style JSON error (not raw 500) when default_provider is absent from providers
- [x] #7 # _validate_admin_authorization uses secrets.compare_digest for the admin token
- [x] #8 # ambiguous_bands band/prefer keys are validated at config load (load-time failure, not silent per-request fallback)
- [x] #9 # doctor and the scorer resolve the classifier models directory via one shared resolver so they never disagree
- [x] #10 # Dead helpers _eligible_primary_candidates, _eligible_fallback_candidates, _fallback_tier (router.py) and unused EMBEDDING_DIM (feature_training.py) are removed
- [x] #11 # Full CI bar passes: ruff check, ruff format check, pyright, pytest; existing tests updated where behavior changed
- [x] #12 # The embedding timeout is additionally enforced at the OpenAI/HTTP client layer (timeout passed to the client) so the bound holds even if the executor pattern changes
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
# Implementation Plan: TASK-8 — Fix Major findings from codebase review (doc-3)

## Approach / Rationale

The fixes touch the scoring hot path (scorer.py, router.py), config loading (config.py, cli.py), the proxy (proxy.py), and dead code cleanup (router.py, feature_training.py). The design principle is: **make expensive resources process-scoped and memoized, not per-request**, while keeping the existing fallback semantics intact.

### Fix 1: Embedding timeout ineffective (AC #1, #12)

**Problem:** `_embed_text` (scorer.py:633-656) uses `with concurrent.futures.ThreadPoolExecutor(...)`. When `future.result(timeout=...)` raises `TimeoutError`, the `with` block exit calls `shutdown(wait=True)`, which blocks until the running thread finishes. The OpenAI client is also constructed without a timeout, so it defaults to ~600s.

**Fix:**
1. Replace the `with` context manager with an explicit `executor = ThreadPoolExecutor(max_workers=1)`, then on timeout call `executor.shutdown(wait=False, cancel_futures=True)` before raising.
2. Pass `timeout=settings.timeout_seconds` to the `OpenAI(...)` client constructor in `_resolve_runtime_embedding_client` (scorer.py:408) so the HTTP layer enforces the bound as a second defense (AC #12).

**Test:** `test_embedding_timeout_aborts_within_bound` — use a `_FakeEmbeddings` with a 2s delay and `timeout_seconds=0.05`, assert the call returns within <1s (not 2s), and assert the result is the default fallback. Existing `test_embedding_timeout_returns_default_fallback` already covers the fallback path but uses a 0.05s delay with 0.001 timeout — we need a stronger test that verifies the *wall-clock* bound is respected, not just that a TimeoutError is raised.

### Fix 2: Routing hot-path re-inits Scorer per request (AC #2, #3, #4)

**Problem:** `Router._classify` (router.py:926) constructs a fresh `Scorer(...)` on every `route()` call. The Scorer's classifier cache is per-instance, so `feature_classifier.pkl` is re-loaded each time. `_resolve_runtime_embedding_settings()` (scorer.py:347) calls `load_config()` which re-parses `config.yaml` on every embedding call. In local mode, `LocalEmbeddingBackend(settings.model)` is instantiated per call (scorer.py:648), reloading the sentence-transformers model from disk each time.

**Fix:**
1. **Persistent Scorer on Router:** Add `self._scorer: Scorer | None = None` to `Router.__init__`. In `_classify`, lazily create and cache a `Scorer` instance keyed by the current config's `ambiguous_bands` and `disable_axis_overrides`. When config reloads (new `RuntimeState`), a new `Router` is constructed (proxy.py:120), so the cached scorer naturally resets. The Scorer already caches the classifier load (`_feature_classifier_load_attempted` flag).
2. **Memoize embedding settings:** Add a module-level cache in scorer.py: `_embedding_settings_cache: dict[str, RuntimeEmbeddingSettings] = {}` keyed by a config fingerprint (path + mtime, or the config object identity). `_resolve_runtime_embedding_settings()` checks the cache first. On config reload, the Router is reconstructed, which constructs a new Scorer — the Scorer can invalidate the cache in `__init__`.
3. **Reuse LocalEmbeddingBackend:** Add a module-level `_local_embedding_backend: LocalEmbeddingBackend | None = None` keyed by model name. In `_embed_text`, reuse the cached instance instead of constructing a new one each call.

**Test for AC #2:** `test_router_reuses_scorer_across_calls` — patch `Scorer.__init__` to count calls, call `router.route()` multiple times, assert Scorer is constructed at most once.

**Test for AC #3:** `test_embedding_settings_are_memoized` — patch `load_config` to count calls, call `_resolve_runtime_embedding_settings()` multiple times, assert `load_config` is called at most once (subsequent calls hit cache).

**Test for AC #4:** `test_local_embedding_backend_reused` — patch `LocalEmbeddingBackend.__init__` to count calls, call `_embed_text` multiple times in local mode, assert the constructor is called at most once.

### Fix 3: Config file encoding (AC #5)

**Problem:** `config.py:553` uses `open(config_file)` and `cli.py:124` uses `raw_path.open()` without `encoding="utf-8"`. On Windows (cp1252), non-ASCII YAML raises `UnicodeDecodeError`.

**Fix:** Add `encoding="utf-8"` to both `open()` calls.

**Test:** `test_config_loads_non_ascii_yaml` — write a config with a non-ASCII comment or string value (e.g. a model name with accented characters), load it, assert it succeeds. This test runs on all platforms but is specifically guarding Windows behavior.

### Fix 4: Pass-through KeyError → structured error (AC #6)

**Problem:** `_get_default_provider_info` (proxy.py:495) does `state.config.providers[dp_name]` which raises raw `KeyError` if `default_provider` isn't in `providers`, resulting in a FastAPI 500 HTML response.

**Fix:** In `chat_completions`, wrap the pass-through branch: if `dp_name not in state.config.providers`, return `_openai_error(500, f"Default provider '{dp_name}' is not configured", "server_error")`. Alternatively, make `_get_default_provider_info` raise a `ValueError` with a clear message and catch it in the handler. The cleaner approach: check `dp_name in state.config.providers` before calling `_get_default_provider_info` and return a structured error.

**Test:** `test_passthrough_returns_structured_error_when_provider_missing` — configure a proxy where `default_provider` references a non-existent provider, send a pass-through request, assert 500 with OpenAI-style JSON error body.

### Fix 5: Admin token compare_digest (AC #7)

**Problem:** `proxy.py:1640` uses `token != expected` — timing attack vector.

**Fix:** Replace with `secrets.compare_digest(token, expected)`. Import `secrets` at the top of proxy.py (check if already imported).

**Test:** Existing `test_reload_rejected_with_invalid_token` covers the behavior. Add `test_admin_token_uses_compare_digest` — patch `secrets.compare_digest` to verify it's called (or verify the existing tests still pass with the change).

### Fix 6: ambiguous_bands load-time validation (AC #8)

**Problem:** `_ambiguous_bands_normalized` (scorer.py:156) raises `ValueError` on bad config, but it runs inside `classify()`'s broad `except Exception` → silent fallback. A config typo silently degrades every request.

**Fix:** Add a `@model_validator(mode="after")` to `OptiproxaiConfig` that calls `_ambiguous_bands_normalized(self.ambiguous_bands)` and raises `ValueError` if invalid. This makes bad config fail at load time. Import `_ambiguous_bands_normalized` from scorer in config.py (or inline the validation to avoid a circular import — better to inline since config.py shouldn't depend on scorer.py).

**Test:** `test_invalid_ambiguous_bands_fails_at_load_time` — construct an `OptiproxaiConfig` with bad `ambiguous_bands` (e.g. unknown boundary key, invalid prefer value), assert `ValidationError` is raised.

### Fix 7: Shared models-directory resolver (AC #9)

**Problem:** `cli.py:191` defaults to `Path.cwd() / "models"` while `scorer.py:329` uses `Path(__file__).resolve().parents[2] / "models"`. Doctor can report the classifier missing while runtime finds it.

**Fix:** Export `_default_model_dir()` from scorer.py (rename to `default_model_dir()` — public) and use it in `cli.py:191` as the default for `build_doctor_results`. The `--models-dir` CLI flag still overrides.

**Test:** `test_doctor_uses_same_model_dir_as_scorer` — call `build_doctor_results` without `models_dir`, assert the resolved path matches `default_model_dir()`.

### Fix 8: Dead code removal (AC #10)

**Problem:** `_eligible_primary_candidates`, `_eligible_fallback_candidates` (router.py:640-672), `_fallback_tier` (router.py:982-988), and `EMBEDDING_DIM` (feature_training.py:28) are never called.

**Fix:** Remove all four. Grep confirmed zero references outside their definitions.

**Test:** No new test needed — removal is verified by the existing test suite passing. Pyright will confirm no dangling references.

## Files to Modify

| File | Changes |
|------|---------|
| `src/optiproxai/scorer.py` | Fix executor shutdown (AC #1); pass timeout to OpenAI client (AC #12); memoize embedding settings (AC #3); reuse LocalEmbeddingBackend (AC #4); export `default_model_dir()` (AC #9) |
| `src/optiproxai/router.py` | Cache persistent Scorer on Router (AC #2); remove dead helpers `_eligible_primary_candidates`, `_eligible_fallback_candidates`, `_fallback_tier` (AC #10) |
| `src/optiproxai/config.py` | Add `encoding="utf-8"` to `open()` (AC #5); add `ambiguous_bands` model validator (AC #8) |
| `src/optiproxai/cli.py` | Add `encoding="utf-8"` to `raw_path.open()` (AC #5); use `default_model_dir()` from scorer (AC #9) |
| `src/optiproxai/proxy.py` | Structured error on missing default provider (AC #6); `secrets.compare_digest` for admin token (AC #7) |
| `src/optiproxai/feature_training.py` | Remove unused `EMBEDDING_DIM` constant (AC #10) |
| `tests/test_scorer.py` | New tests: timeout wall-clock bound (AC #1), embedding settings memoized (AC #3), local backend reused (AC #4) |
| `tests/test_proxy_reload.py` | New test: structured error on missing provider (AC #6); admin token compare_digest (AC #7) |
| `tests/test_config.py` | New tests: non-ASCII YAML loads (AC #5), invalid ambiguous_bands fails at load (AC #8) |
| `tests/test_cli.py` | New test: doctor uses shared model dir resolver (AC #9) |
| `tests/test_router_logging.py` or new `tests/test_router_scorer.py` | New test: Scorer constructed once across multiple route() calls (AC #2) |

## Constraints / Risks / Open Questions

1. **Circular import risk (AC #8):** `config.py` importing `_ambiguous_bands_normalized` from `scorer.py` could create a circular import since `scorer.py` imports from `config.py`. Solution: inline the validation logic in `OptiproxaiConfig`'s validator rather than importing from scorer. The validation is small (check keys against a tuple of valid boundary names, check `prefer` is LOWER/UPPER, check `band >= 0`).

2. **Cache invalidation (AC #3):** The embedding settings cache must be invalidated when config reloads. Since `_build_runtime_state` constructs a new `Router` (which constructs a new `Scorer`), the Scorer's `__init__` can clear the module-level cache. But if `_resolve_runtime_embedding_settings` is called from `DistilledFeatureClassifier.from_bundle` (which is called during `Scorer._load_feature_classifier`), the cache must be populated before the classifier loads. The flow: Router constructed → Scorer cached → first `route()` → `_classify` → `scorer.classify()` → `_load_feature_classifier()` → `DistilledFeatureClassifier.load()` → `from_bundle()` → `_resolve_runtime_embedding_settings()`. The cache should be checked/populated in `_resolve_runtime_embedding_settings` itself, keyed by config file path + mtime.

3. **Thread safety of caches:** The embedding settings cache and local backend cache are module-level. The proxy is async (single-threaded event loop), and the CLI is single-threaded. Thread pool executors are used for embedding calls but they don't write to the cache. A simple dict without locks is sufficient for the current architecture. If future multi-threaded use is introduced, a `threading.Lock` can be added.

4. **Test for AC #12 (OpenAI client timeout):** The OpenAI client timeout is tested indirectly by the timeout test (AC #1). We can also assert that `OpenAI(...)` is called with `timeout=` in the test, or check the constructed client's `.timeout` attribute.

5. **Breaking change check:** The `default_model_dir()` rename from `_default_model_dir()` to public is a minor API change. Since it's an internal helper (underscore-prefixed), making it public is additive. The doctor CLI already accepts `--models-dir` override; this only changes the *default* when the flag is omitted, aligning it with runtime behavior. This is a correctness fix, not a breaking change.

## Task Manifest

| # | Title | Files | Depends On | Labels | Acceptance Criterion |
|---|---|---|---|---|---|
| 1 | Fix embedding timeout executor shutdown + OpenAI client timeout | src/optiproxai/scorer.py, tests/test_scorer.py | — | logic, test | A stalled embedding call aborts within the configured timeout (wall-clock verified) and the OpenAI client is constructed with timeout=settings.timeout_seconds |
| 2 | Cache persistent Scorer on Router; memoize embedding settings; reuse LocalEmbeddingBackend | src/optiproxai/scorer.py, src/optiproxai/router.py, tests/test_scorer.py, tests/test_router_scorer.py | 1 | logic, test | Scorer is constructed at most once per Router lifetime, embedding settings are cached per config, and LocalEmbeddingBackend is reused across calls |
| 3 | Fix config encoding + ambiguous_bands load-time validation | src/optiproxai/config.py, src/optiproxai/cli.py, tests/test_config.py | — | logic, test | Non-ASCII YAML loads on all platforms and invalid ambiguous_bands raises ValidationError at config load time |
| 4 | Fix pass-through structured error + admin token compare_digest | src/optiproxai/proxy.py, tests/test_proxy_reload.py | — | logic, test | Missing default_provider returns OpenAI-style JSON error and admin token uses secrets.compare_digest |
| 5 | Unify models-directory resolver + remove dead code | src/optiproxai/scorer.py, src/optiproxai/cli.py, src/optiproxai/router.py, src/optiproxai/feature_training.py, tests/test_cli.py | 2 | logic, test | Doctor and scorer use the same default models directory and dead helpers/EMBEDDING_DIM are removed |
| 6 | Run full CI bar and fix regressions | — | 1, 2, 3, 4, 5 | infra | ruff check, ruff format check, pyright, and pytest all pass with no regressions |
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implementation complete on branch task/TASK-8.

All 12 acceptance criteria verified with tests.

Full CI bar passes: ruff check clean, ruff format clean, pyright 0 errors, 318/318 tests pass, uv build succeeds.

11 new tests added across test_scorer.py, test_router_logging.py, test_config.py, test_proxy_reload.py, test_cli.py.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Fixed all Major findings from doc-3 codebase review:

1. **Embedding timeout (AC #1, #12):** `_embed_text` now uses `executor.shutdown(wait=False, cancel_futures=True)` in a `finally` block instead of the blocking `with` context manager, so a stalled upstream aborts within the configured bound. The OpenAI client is also constructed with `timeout=settings.timeout_seconds` as a second defense layer.

2. **Routing hot-path re-init (AC #2, #3, #4):** Router now holds a persistent `Scorer` instance (constructed lazily on first `_classify` call, reset on config reload via new Router). Embedding settings are memoized per config fingerprint (path + mtime). LocalEmbeddingBackend is reused across calls via a module-level cache.

3. **Config encoding (AC #5):** `open(config_file, encoding="utf-8")` in config.py and `raw_path.open(encoding="utf-8")` in cli.py.

4. **Pass-through structured error (AC #6):** Missing default_provider now returns OpenAI-style JSON error (500, server_error) instead of raw KeyError.

5. **Admin token compare_digest (AC #7):** `_validate_admin_authorization` uses `secrets.compare_digest`.

6. **ambiguous_bands load-time validation (AC #8):** `OptiproxaiConfig._validate_ambiguous_bands` model validator fails at load time on invalid band/prefer keys.

7. **Shared models-dir resolver (AC #9):** `_default_model_dir()` renamed to public `default_model_dir()` and used by both doctor and scorer.

8. **Dead code removal (AC #10):** Removed `_eligible_primary_candidates`, `_eligible_fallback_candidates`, `_fallback_tier` from router.py and `EMBEDDING_DIM` from feature_training.py.

**Test outcome:** 318/318 tests pass (307 existing + 11 new). ruff check clean, ruff format clean, pyright 0 errors, uv build succeeds.
<!-- SECTION:FINAL_SUMMARY:END -->
