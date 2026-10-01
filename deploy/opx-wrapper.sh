#!/usr/bin/env bash
# opx — run the OptiProxAI CLI with the service environment loaded.
#
# Why this exists: api_keys, logs, and dashboard data are located by
# OPTIPROXAI_DATA_DIR / OPTIPROXAI_LOG_DIR, which live in
# /etc/optiproxai/optiproxai.env. Running the CLI without sourcing that file
# makes it look in the default XDG path (~/.local/share/optiproxai) and miss
# the real data (e.g. `keys remove` reporting "No API key found").
#
# Install to /usr/local/bin/opx, mode 755. Run as the service account:
#   sudo -u optiproxai opx keys list
#   sudo -u optiproxai opx keys add cursor
#   sudo -u optiproxai opx route "explain quicksort"
set -euo pipefail

ENV_FILE="/etc/optiproxai/optiproxai.env"
REPO="/opt/optiproxai"

if [[ ! -r "$ENV_FILE" ]]; then
    echo "opx: cannot read $ENV_FILE (run as the optiproxai user)" >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

cd "$REPO"
exec "$REPO/.venv/bin/optiproxai" "$@"
