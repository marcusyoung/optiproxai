# Deploying OptiProxAI on a VPS

Reference deployment for a single Ubuntu host running OptiProxAI behind an
existing Apache reverse proxy with TLS. Copy the files to the paths noted below;
do not run them from this directory.

## Files

| File | Install to | Purpose |
|------|-----------|---------|
| `optiproxai.service` | `/etc/systemd/system/optiproxai.service` | systemd unit (hardened, runs the venv console script) |
| `optiproxai.env.example` | `/etc/optiproxai/optiproxai.env` | provider keys, admin token, state paths (root:optiproxai, 640) |
| `apache-optiproxai.conf` | `/etc/apache2/sites-available/optiproxai.conf` | Apache vhost; certbot adds the TLS block |
| `config.vps.yaml` | `/opt/optiproxai/config.yaml` | example config for a server deployment (loopback bind, `tools_capability_detection: active`) |
| `opx-wrapper.sh` | `/usr/local/bin/opx` | CLI wrapper that sources the env file |
| `opx-annotate.sh` | `/usr/local/bin/opx-annotate` | build/extend the training dataset from routing logs |
| `opx-train.sh` | `/usr/local/bin/opx-train` | train `models/feature_classifier.pkl` |
| `Caddyfile` | `/etc/caddy/Caddyfile` | alternative to Apache for hosts running Caddy |

## Layout

```text
/opt/optiproxai          # editable install (uv sync); Git checkout
  .venv/                 # project venv (runs the service)
  config.yaml            # from config.vps.yaml
  models/feature_classifier.pkl
/etc/optiproxai/optiproxai.env
/var/log/optiproxai/     # server.log (console), routing-*.jsonl, execution-*.jsonl (OPTIPROXAI_LOG_DIR)
/var/lib/optiproxai/     # dashboard.db, api_keys.json, dataset, cache (OPTIPROXAI_DATA_DIR)
```

## Install outline

1. Create the service account and `uv`, then install Python 3.13 (Ubuntu 24.04
   ships 3.12; `uv` manages its own interpreter, so system Python is untouched):

   ```bash
   sudo useradd --system --create-home --shell /usr/sbin/nologin optiproxai
   sudo -u optiproxai -H bash -lc 'curl -LsSf https://astral.sh/uv/install.sh | sh'
   sudo -u optiproxai -H bash -lc 'cd ~ && /home/optiproxai/.local/bin/uv python install 3.13'
   ```

2. Clone and install. `uv sync` is the heavy step (PyTorch via
   `sentence-transformers`):

   ```bash
   sudo mkdir -p /opt/optiproxai && sudo chown optiproxai:optiproxai /opt/optiproxai
   sudo -u optiproxai -H git clone https://github.com/marcusyoung/optiproxai.git /opt/optiproxai
   sudo -u optiproxai -H bash -lc 'cd /opt/optiproxai && /home/optiproxai/.local/bin/uv sync'
   ```

   `uv` installs the project **editable**, so the classifier resolves to
   `/opt/optiproxai/models/feature_classifier.pkl` (`scorer.py` walks
   `parents[2]` from the package root). Verify with:

   ```bash
   sudo -u optiproxai -H bash -lc 'cd /opt/optiproxai && .venv/bin/python -c "import optiproxai.scorer as s; print(s._feature_classifier_path(None))"'
   ```

3. Install config and secrets (`config.yaml` and `models/` are gitignored, so
   they are supplied out-of-band), then the unit:

   ```bash
   sudo install -o optiproxai -g optiproxai -m 640 config.vps.yaml /opt/optiproxai/config.yaml
   sudo mkdir -p /etc/optiproxai
   sudo install -o root -g optiproxai -m 640 optiproxai.env.example /etc/optiproxai/optiproxai.env
   sudo nano /etc/optiproxai/optiproxai.env    # fill in keys
   sudo mkdir -p /var/log/optiproxai /var/lib/optiproxai
   sudo chown optiproxai:optiproxai /var/log/optiproxai /var/lib/optiproxai
   sudo install -o root -g root -m 644 optiproxai.service /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl enable --now optiproxai
   ```

4. Issuing a proxy API key makes authentication mandatory (without one, the
   proxy accepts every request):

   ```bash
   sudo -u optiproxai opx keys add cursor
   ```

5. Reverse proxy + TLS:

   ```bash
   sudo a2enmod proxy proxy_http
   sudo a2ensite optiproxai && sudo apache2ctl configtest && sudo systemctl reload apache2
   sudo certbot --apache -d <your-hostname>
   ```

## Authentication

OptiProxAI handles auth itself; the reverse proxy needs no htpasswd and no
header injection.

- **API clients** send `Authorization: Bearer <key>` on every request.
- **Browsers** reach `/dashboard` with HTTP Basic, where the password is the
  same OptiProxAI API key (username ignored). The proxy returns a
  `WWW-Authenticate: Basic` challenge so the browser prompts.
- Basic is deliberately **not** accepted on `/v1/*` (browsers auto-attach
  cached Basic credentials, which would make API routes CSRF-reachable).
- `/health`, `/docs`, `/openapi.json`, and `/admin/reload-config` are exempt.

Because the key is not duplicated in proxy config, rotation is a single step:

```bash
sudo -u optiproxai opx keys remove <name>
sudo -u optiproxai opx keys add <name>
```

## Retraining

The service logs every routing decision to `OPTIPROXAI_LOG_DIR`, which is what
the training pipeline consumes. Both wrappers source the service environment, so
run them as the service account:

```bash
sudo -u optiproxai opx-annotate            # annotate new records (MISTRAL_API_KEY)
sudo -u optiproxai opx-annotate --train    # annotate, then train (VOYAGE_API_KEY)
sudo -u optiproxai opx-train               # train only
sudo systemctl restart optiproxai          # the classifier is cached after first load
```

Training writes `/opt/optiproxai/models/feature_classifier.pkl` in place. Keep a
backup first if you want a rollback.

## Notes

- `config.vps.yaml` binds `127.0.0.1`; only the reverse proxy is exposed.
- `config.yaml` resolves `${VAR}` from the **process environment only** — there
  is no `.env` auto-load. systemd's `EnvironmentFile` is what provides it, and a
  missing variable resolves to an empty string rather than an error.
- The unit sets `ProtectHome=read-only`, which keeps the `uv`-managed Python
  under `/home/optiproxai` readable while blocking writes to it.
