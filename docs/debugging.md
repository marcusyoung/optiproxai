# Routing debugging

Practical notes for when the model you got is not the one you expected.
Everything here is about *observable behavior* of a running proxy; config
syntax for profiles/tiers/`primary_selection` lives in `README.md` and
`config.example.yaml`.

## The response `model` is the upstream's self-reported name, not optiproxai's choice

optiproxai forwards the upstream response body verbatim, so the `model` field
in the client response is whatever the **provider** returned — not necessarily
the primary you configured.

Concrete trap: a tier primary `syn:large:vision` on the `synthetic` provider is
served successfully, but synthetic returns `"model": "moonshotai/Kimi-K3"`
(its internal name for that model). If your config *also* defines
`moonshotai/Kimi-K3` as a **fallback** model on another provider (e.g.
`sference`), the response looks like a fallback even though the primary
succeeded. It was not a fallback.

To find the true upstream, use the routing log `provider` field or the proxy
`USAGE` line, both of which record the actually-served provider:

```
USAGE ... model=syn:large:vision provider=synthetic ...  # served by synthetic
```

The `USAGE` line is only emitted on a **successful** completion.

## The routing JSONL records the primary decision, not the served model

- Location: `$XDG_STATE_HOME/optiproxai/log/routing-YYYY-MM-DD.jsonl`
  (default `~/.local/state/optiproxai/log/`).
- Each record has `tier`, `model`, `provider`, `profile`, `score`. The
  `model`/`provider` here are the **primary candidate optiproxai selected**,
  resolved *before* any fallback promotion. If the primary errors and a
  fallback serves the request, the JSONL still shows the primary; only the
  `USAGE` stderr line reflects what actually answered.

So "the log says `kimi-k3`" means the primary was `kimi-k3`, not that
`kimi-k3` necessarily served the token.

## session_sticky selection is deterministic per key

`primary_selection: session_sticky` (per tier) picks a primary via:

```
index = hash(session_key) % len(primary_candidates)
hash  = int.from_bytes(sha256(session_key.encode()).digest()[:8], "big")
```

- `session_key` is read from the header named by `routing.session_header`
  (default `X-Session-Id`).
- Within one session (same key) the choice is fixed. Across distinct keys it
  is ~uniform, so a *stable* client session id pins every request in that
  session to one primary. "Always lands on provider X" usually means the
  client reuses one session id that hashes to X — that is working as designed,
  not a bug.
- With two primaries, the choice is a single parity bit of the hash, so expect
  a ~50/50 split across many *different* session keys.

`/v1/route` does **not** read the session header and cannot test sticky
(see `proxy.py` route_debug — it never passes `session_key`). Test against
`/v1/chat/completions`.

## Verifying selection live

Force the tier with `/optiproxai:<tier>` and vary the session id; the
returned `model` is the chosen primary (or its upstream alias):

```powershell
1..6 | ForEach-Object {
  $id = "verify-$_-$(Get-Random)"
  $r = curl.exe -s http://localhost:18421/v1/chat/completions `
    -H "Content-Type: application/json" -H "X-Session-Id: $id" `
    -d '{"model":"optiproxai/auto","messages":[{"role":"user","content":"/optiproxai:reasoning prove P != NP"}]}' `
    | ConvertFrom-Json
  Write-Host "$id -> $($r.model)"
}
```

(Swap `localhost:18421` for your listen port. `curl.exe` — not `curl` — in
PowerShell, which aliases `Invoke-WebRequest`.)

## Primary failures are not logged with a reason

When a primary fails, the proxy logs only a generic WARNING:

```
FALLBACK [1/2] model=... provider=... (primary=<model> failed, source_idx=0)
```

The upstream **status code and error body are returned to the client** but are
**not written to the server log**. So if a primary is failing and a fallback
covers it, your server log shows the fallback warning but not *why* the primary
failed. To see the cause:

- inspect the client response `error` field for the failing request, or
- add upstream status+body logging to `_try_with_fallbacks`
  (`src/optiproxai/proxy.py`) and restart.

In this deployment the proxy's stderr — including the `FALLBACK` warnings above —
is captured by `optiproxai-serve.ps1` to `~/.local/bin/optiproxai-server.log`.
That file is the place to pull error lines from; it is the live proxy's stderr,
not the routing JSONL. Grep it for `FALLBACK` / `Upstream` / the model name to
see why a primary failed.

## Test / reproduction commands

optiproxai does **not** auto-load a `.env`; config resolves `${VAR}` from the
process environment. In this deployment the `optiproxai-serve.ps1` wrapper does
the export — it parses `~/.config/optiproxai/.env` (and a couple of keys from
`~/.secrets/`) into the environment, then runs
`uv run optiproxai serve --config …`, redirecting the proxy's stderr to
`~/.local/bin/optiproxai-server.log`. For a one-off test instance (below) export
keys yourself and point at a free port. All snippets run from the repo root with
`uv run`.

### Offline: verify session_sticky selection (no proxy, no keys)

Exercises the real `Router` against your config — proves determinism per key
and round-robin fallback when no header is sent.

```python
from optiproxai.config import load_config
from optiproxai.router import Router, _session_hash

config = load_config()          # auto-discovers ~/.config/optiproxai/config.yaml
router = Router(config)

for profile in ("code", "analysis"):
    tier = config.profiles[profile].tiers["REASONING"]
    cands = tier.resolve_primary_candidate_entries()
    n = len(cands)
    print(f"{profile} REASONING primary_selection={tier.primary_selection} n={n}")
    for i, c in enumerate(cands):
        print(f"  [{i}] {c.model} @ {c.provider}")
    for key in ("session-A", "session-B", "alpha", "beta"):
        sel = router._select_primary_candidate(profile, "REASONING", tier, session_key=key)
        print(f"  key={key!r} idx={_session_hash(key) % n} -> {sel.model} @ {sel.provider}")
    for _ in range(4):   # no header -> round-robin
        sel = router._select_primary_candidate(profile, "REASONING", tier, session_key=None)
        print(f"  round-robin -> {sel.model} @ {sel.provider}")
```

### Offline: distribution across distinct session keys

Confirms selection spreads ~uniformly across distinct keys (≈50/50 with two
primaries). With a *stable* key it pins to one index.

```python
import random
from optiproxai.config import load_config
from optiproxai.router import Router
config = load_config(); router = Router(config)
tier = config.profiles["code"].tiers["REASONING"]
cands = tier.resolve_primary_candidate_entries()
cnt = [0] * len(cands)
for _ in range(4000):
    k = f"opencode-{random.randint(0, 10**12):012x}"
    sel = router._select_primary_candidate("code", "REASONING", tier, session_key=k)
    cnt[cands.index(sel)] += 1
for i, c in enumerate(cands):
    print(f"  {c.model} @ {c.provider}: {cnt[i]}")
```

### Inspect what was actually selected in live traffic

Scans the routing JSONL for REASONING decisions (primary choice + provider).
The `provider` here is the primary optiproxai selected, not necessarily what
served the token (see above).

```python
import glob, json, os
from collections import Counter
files = sorted(glob.glob(os.path.expanduser("~/.local/state/optiproxai/log/routing-*.jsonl")))
recs = [json.loads(l) for f in files for l in open(f, encoding="utf-8") if l.strip()]
reason = [r for r in recs if r.get("tier") == "REASONING"]
print("REASONING decisions:", len(reason))
print(Counter((r.get("model"), r.get("provider"), r.get("profile")) for r in reason))
```

### End-to-end: run the proxy on a test port, send a sticky request, read the log

Starts a second instance (does not disturb a live one), forces REASONING, and
sends a key that hashes to index 1 (`verify-3-5801476` → `syn:large:vision` for
the `code` profile). The `USAGE` line proves which provider actually served it;
the response `model` shows the upstream's self-reported name.

```bash
set -a; . /path/to/.env; set +a          # export keys (optiproxai won't load .env)
uv run optiproxai serve --port 18499 > /tmp/proxy.log 2>&1 &
sleep 12
curl -s http://localhost:18499/v1/chat/completions \
  -H "Content-Type: application/json" -H "X-Session-Id: verify-3-5801476" \
  -d '{"model":"optiproxai/auto","messages":[{"role":"user","content":"/optiproxai:reasoning prove P != NP"}]}' \
  | python -c "import sys,json;d=json.load(sys.stdin);print('model=',d.get('model'),'error=',d.get('error'))"
kill %1
grep -Ei "FALLBACK|USAGE|syn:large|REASONING" /tmp/proxy.log | tail -20
```

Change the `X-Session-Id` value to vary the selected index.

### Direct provider probe (bypass optiproxai entirely)

To confirm a provider/model actually works without optiproxai in the path —
rules out "the provider is down" vs "optiproxai's request shape is rejected":

```bash
curl -s https://api.synthetic.new/openai/v1/chat/completions \
  -H "Authorization: Bearer $SYNTHETIC_API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"syn:large:vision","messages":[{"role":"user","content":"hi"}]}'
```

If this returns 200 but optiproxai falls back, compare the request body
optiproxai sends (e.g. the `reasoning_effort` injection at REASONING tier) against
what the provider accepts.
