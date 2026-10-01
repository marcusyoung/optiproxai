#!/usr/bin/env bash
# opx-train — train the distilled feature classifier from the annotated dataset.
#
# Linux equivalent of opx-train.ps1. Sources the service environment for
# VOYAGE_API_KEY (the embedding provider configured in config.yaml) and runs
# from the repo root so the config `embedding` block resolves.
#
# Writes /opt/optiproxai/models/feature_classifier.pkl in place — the exact path
# the running service reads. The service caches the classifier after first load,
# so restart it afterwards.
#
# Install to /usr/local/bin/opx-train, mode 755. Run as the service account:
#   sudo -u optiproxai opx-train
set -euo pipefail

ENV_FILE="/etc/optiproxai/optiproxai.env"
REPO="/opt/optiproxai"

if [[ ! -r "$ENV_FILE" ]]; then
    echo "opx-train: cannot read $ENV_FILE (run as the optiproxai user)" >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

# Derive paths AFTER sourcing the env file, so a non-default
# OPTIPROXAI_DATA_DIR takes effect before the dataset is validated.
DATADIR="${OPTIPROXAI_DATA_DIR:-/var/lib/optiproxai}"
DATASET="$DATADIR/distilled_feature_dataset.json"

if [[ ! -f "$DATASET" ]]; then
    echo "opx-train: dataset not found: $DATASET (run opx-annotate first)" >&2
    exit 1
fi

cd "$REPO"

echo "==> Training feature classifier from $DATASET"
"$REPO/.venv/bin/python" scripts/train_classifier.py \
    --data "$DATASET" \
    --output "$REPO/models" \
    --cache "$DATADIR/cache"

echo "==> Classifier written to $REPO/models/feature_classifier.pkl"
echo "==> Restart to load it: sudo systemctl restart optiproxai"
