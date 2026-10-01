#!/usr/bin/env bash
# opx-annotate — build/extend the distilled feature training dataset from routing logs.
#
# Linux equivalent of opx-annotate.ps1. Sources the service environment (which
# already holds MISTRAL_API_KEY for the feature_annotator and VOYAGE_API_KEY for
# training) so nothing has to be exported by hand.
#
# Install to /usr/local/bin/opx-annotate, mode 755. Run as the service account:
#   sudo -u optiproxai opx-annotate            # annotate records missing labels
#   sudo -u optiproxai opx-annotate --force    # re-annotate everything
#   sudo -u optiproxai opx-annotate --train    # annotate, then train
#
# Requires paths in the service env (OPTIPROXAI_LOG_DIR / OPTIPROXAI_DATA_DIR);
# defaults below match the systemd unit in this directory.
set -euo pipefail

ENV_FILE="/etc/optiproxai/optiproxai.env"
REPO="/opt/optiproxai"
LOGDIR="${OPTIPROXAI_LOG_DIR:-/var/log/optiproxai}"
DATADIR="${OPTIPROXAI_DATA_DIR:-/var/lib/optiproxai}"
DATASET="$DATADIR/distilled_feature_dataset.json"

FORCE=0
TRAIN=0
for arg in "$@"; do
    case "$arg" in
        --force|-f) FORCE=1 ;;
        --train|-t) TRAIN=1 ;;
        *)
            echo "opx-annotate: unknown option: $arg" >&2
            exit 2
            ;;
    esac
done

if [[ ! -r "$ENV_FILE" ]]; then
    echo "opx-annotate: cannot read $ENV_FILE (run as the optiproxai user)" >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

cd "$REPO"

ANNOTATE_FLAG="--annotate-missing"
[[ "$FORCE" == "1" ]] && ANNOTATE_FLAG="--force-annotate"

echo "==> Annotating routing logs from $LOGDIR ($ANNOTATE_FLAG)"
"$REPO/.venv/bin/python" scripts/build_agentic_dataset.py \
    "$ANNOTATE_FLAG" \
    --log-dir "$LOGDIR" \
    --output "$DATASET"

if [[ "$TRAIN" == "1" ]]; then
    echo "==> Training classifier from $DATASET"
    "$REPO/.venv/bin/python" scripts/train_classifier.py \
        --data "$DATASET" \
        --output "$REPO/models" \
        --cache "$DATADIR/cache"
    echo "==> Restart to load the new classifier: sudo systemctl restart optiproxai"
fi
