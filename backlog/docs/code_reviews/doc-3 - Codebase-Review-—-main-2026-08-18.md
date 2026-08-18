---
id: doc-3
title: Codebase Review — main (2026-08-18)
type: other
created_date: '2026-08-18 14:08'
---
# Codebase Review — main (2026-08-18)

**Scope:** Full review of `src/optiproxai/` (15 modules, ~8,070 LoC) plus test surface, based on the branch-review standards adapted for a whole-codebase review (no diff — every finding is assessed against current main).

**Health baseline:** 307/307 tests pass, ruff clean, pyright 0 errors. CI bar is green.

## Summary

The codebase is in good shape: typed, consistently styled, well-tested, and passes the full CI bar (ruff, pyright, pytest). The architecture is clean (CLI → config/router → scorer/proxy). The main concerns cluster in the scoring path: the embedding timeout is defeated by executor shutdown semantics, and the routing hot path re-does cold work (pickle load, YAML+config load, local-model load) on every request. A handful of smaller correctness/security issues exist around config file encoding, the pass-through endpoint's missing-provider crash, and admin-token comparison.

**Recommendation: Needs changes.** No Critical findings, but two Major findings affect production behavior.

## Findings

### MAJOR

#### 1. `scorer.py` — Embedding timeout is ineffective; request hangs until the underlying call completes [HIGH confidence]
In `_embed_text` (lines 633–656), a `TimeoutError` is raised *inside* the `with concurrent.futures.ThreadPoolExecutor(...)` block. Exiting the `with` calls `shutdown(wait=True)`, which blocks until the running embedding request finishes — `future.cancel()` is a no-op on a running thread, and the `OpenAI` client is constructed without a timeout, so it defaults to ~600s. The configured `timeout_seconds` (default 5s) therefore never bounds the wait, and under repeated slow upstreams each request leaks a live thread.

**Fix:** drop the `with` and call `executor.shutdown(wait=False, cancel_futures=True)` after raising, and/or pass `timeout=settings.timeout_seconds` to the `OpenAI(...)` client so the HTTP layer enforces it.

#### 2. `router.py` + `scorer.py` — Routing hot path re-does cold work on every request [HIGH confidence]
`Router._classify` (router.py:926) constructs a fresh `Scorer()` per routing decision. The Scorer's classifier cache is per-instance, so each request re-reads and `pickle.load`s `feature_classifier.pkl` (scorer.py:815–835). Every embedding call then re-parses and validates `config.yaml` via `load_config()` inside `_resolve_runtime_embedding_settings` (scorer.py:347–399/610) and creates a new `OpenAI` client. Worst case is local mode: `LocalEmbeddingBackend(settings.model)` is instantiated per call (scorer.py:648), so the sentence-transformers model is loaded from disk on **every routed request**.

**Fix:** hold a persistent `Scorer` on `Router`, memoize embedding settings, and share a single `LocalEmbeddingBackend`.

#### 3. `config.py:553` / `cli.py:124` — Config file opened without explicit encoding [HIGH confidence]
`open(config_file)` and `raw_path.open()` use the platform default encoding. On Windows (cp1252) a UTF-8 YAML config with any non-ASCII content raises `UnicodeDecodeError`. Every other file access in the codebase pins `encoding="utf-8"`; these two don't.

**Fix:** `open(config_file, encoding="utf-8")`.

### MINOR

#### 4. `proxy.py:495` — Pass-through path crashes with a raw KeyError [HIGH confidence]
`_get_default_provider_info` does `state.config.providers[dp_name]`. If `default_provider` isn't in `providers`, the un-routed branch of `chat_completions` raises an unhandled `KeyError` → FastAPI plain 500 HTML, breaking the OpenAI-style JSON error contract (the routed path returns structured errors).

#### 5. `proxy.py:1640` — Admin token compared with plain `!=` [HIGH confidence]
`_validate_admin_authorization` uses `token != expected`; `api_keys.validate_key` correctly uses `secrets.compare_digest`. Use `compare_digest` for the admin token too.

#### 6. `scorer.py:199–249` + `config.py:373` — Invalid `ambiguous_bands` silently degrades the classifier per-request [HIGH confidence]
`_ambiguous_bands_normalized` raises `ValueError` on bad config, but it runs inside `classify()`'s broad `except Exception` → default fallback. A config typo therefore silently reverts every request to the conservative default tier instead of failing at load time. Fix: validate band keys in `OptiproxaiConfig` (a model validator) so bad config fails loudly.

#### 7. `cli.py:191` vs `scorer.py:328` — Doctor and runtime disagree on the models directory [MEDIUM confidence]
`doctor` defaults to `Path.cwd() / "models"` while the scorer resolves `Path(__file__).resolve().parents[2] / "models"` (repo-root when run from source; a venv-relative path when installed as a package). Doctor can report the classifier missing while runtime finds it, and vice versa. Unify on one resolver.

#### 8. `router.py` — Dead code [HIGH confidence]
`_eligible_primary_candidates`, `_eligible_fallback_candidates`, and `_fallback_tier` are never called anywhere (verified by grep across src/ and tests/).

#### 9. `feature_training.py:28` — Unused constant [HIGH confidence]
`EMBEDDING_DIM = 1024` is defined but never referenced (bundle uses `X.shape[1]`).

#### 10. `api_keys.py:54–57` — Non-atomic key-file write, no permission tightening [MEDIUM confidence]
`_save_keys` writes directly (a crash can corrupt `api_keys.json`), no file-lock, and inherits default permissions. Also `remove_key` matches `name == id OR prefix == id`, so a name that collides with another key's prefix removes both (the CLI partially guards this; the function itself doesn't).

## Cross-cutting

- Import-time side effects: `logger.py` (lines 28–32) and `dashboard.py` (line 18) resolve/create directories at import, so `OPTIPROXAI_LOG_DIR` etc. must be set before the first import — worth documenting or deferring.
- Dashboard SQLite ingest runs synchronously on the event loop on every `/dashboard` hit — acceptable at single-user scale, worth noting if multi-tenant.

## Notes (observations, not defects)

- `_normalize_reasoning_effort` silently maps unrecognized effort strings to `"medium"` — intentional-looking, but a debug log on the fallback would help operators.
- Streaming fallbacks can only trigger on pre-stream failures (`_is_retryable_error` never matches `StreamingResponse`) — inherent limitation, matches test expectations.
- `pickle.load` on `feature_classifier.pkl` is conventional for a local trusted model artifact; flag only if the bundle will ever be distributed/downloaded.
