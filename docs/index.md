---
layout: default
title: OptiProxAI
---

**An OpenAI-compatible local proxy that routes every LLM request to the right model — automatically.**

OptiProxAI classifies each request by prompt complexity, required capabilities, and your cost/quality profile, then sends it to the most suitable model. One endpoint, one API key, all your providers.

## Why OptiProxAI?

- **One endpoint** across OpenAI, OpenRouter, local models, and other providers
- **Lower cost** — simple prompts go to cheap models, not your flagship
- **Better quality** — complex, agentic, and reasoning-heavy work keeps the strong models
- **Full transparency** — every routing decision is visible in headers, JSONL logs, and a live dashboard

## How routing works

```text
Request → Distilled Feature Classifier → Tier + Agentic Score → Capability Filter → Model Selection → Upstream Provider
                                                   │
                                                   └─ model unavailable → conservative default
```

The router uses a deterministic token count plus 14 learned semantic dimensions to score each prompt into one of four tiers: `SIMPLE`, `MEDIUM`, `COMPLEX`, or `REASONING`. Capability filtering escalates to stronger models when a request needs vision, tools, or JSON mode. Runtime routing never calls an LLM — classification is fast and local.

## Quick start

```bash
git clone https://github.com/marcusyoung/optiproxai.git
cd optiproxai
uv sync
cp config.example.yaml config.yaml
```

Set your provider key (for example `OPENROUTER_API_KEY`), then start the proxy:

```bash
uv run optiproxai serve
```

Send a request — no client changes needed:

```bash
curl http://localhost:18420/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "optiproxai/auto",
    "messages": [{"role": "user", "content": "explain quicksort"}]
  }'
```

## Per-turn tier override

Force a tier for a single request by starting the message with `/optiproxai:<tier>`:

```bash
curl http://localhost:18420/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "optiproxai/auto",
    "messages": [{"role": "user", "content": "/optiproxai:reasoning prove P != NP"}]
  }'
```

The token is stripped before the request reaches the upstream provider.

## Why "distilled"?

Routing improves through retraining and calibration rather than runtime prompt engineering. The classifier is a compact model trained on distilled feature datasets, so routing decisions are fast, deterministic, and explainable.

## Links

- [GitHub repository](https://github.com/marcusyoung/optiproxai) — source, issues, and full README
- [MIT License](https://github.com/marcusyoung/optiproxai/blob/main/LICENSE)

---

*Based on [kani](https://github.com/tumf/kani). Scoring logic ported from [ClawRouter](https://github.com/BlockRunAI/ClawRouter).*
