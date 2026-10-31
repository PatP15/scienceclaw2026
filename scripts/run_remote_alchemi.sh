#!/usr/bin/env bash
# Mac-side: push inputs + code to the Brev GPU, run remote/run_alchemi.py there under nohup,
# wait for it, then pull results back into data/alchemi/.
# Usage: scripts/run_remote_alchemi.sh <mode> [extra args for run_alchemi.py]
#   modes: example | selftest | optimize | md        (see remote/run_alchemi.py --help)
# Inputs are generated on the Mac first: uv run python -m fermentai.molecular.structures
set -euo pipefail
# --- shared Brev settings (duplicated in each scripts/*brev*.sh helper) ---
export PATH="$HOME/.local/bin:$PATH"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BREV_INSTANCE="${BREV_INSTANCE:-fermentai-gpu}"
REMOTE_ROOT="${REMOTE_ROOT:-fermentai}"          # relative to the remote $HOME
SSH_OPTS=(-T -o ConnectTimeout=20 -o ServerAliveInterval=30 -o ServerAliveCountMax=6)
ALCHEMI_DIR="$REPO_ROOT/data/alchemi"
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
rssh() { ssh "${SSH_OPTS[@]}" "$BREV_INSTANCE" "$@"; }
rsync_to() { rsync -az -e "ssh ${SSH_OPTS[*]}" "$@"; }
# --- end shared settings ---
MODE="${1:?usage: run_remote_alchemi.sh <example|selftest|optimize|md> [args]}"; shift || true
EXTRA="$*"
[ -f "$ALCHEMI_DIR/inputs/manifest.json" ] || { echo "missing inputs; run structures.py first" >&2; exit 2; }

rssh "mkdir -p $REMOTE_ROOT/remote $REMOTE_ROOT/configs $REMOTE_ROOT/inputs $REMOTE_ROOT/results/logs"
rsync_to --delete "$REPO_ROOT/remote/" "$BREV_INSTANCE:$REMOTE_ROOT/remote/"
rsync_to "$REPO_ROOT/configs/molecular.yaml" "$BREV_INSTANCE:$REMOTE_ROOT/configs/molecular.yaml"
rsync_to --delete "$ALCHEMI_DIR/inputs/" "$BREV_INSTANCE:$REMOTE_ROOT/inputs/"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="results/logs/${MODE}_${STAMP}.log"
echo "launching $MODE on $BREV_INSTANCE, log $LOG"
rssh "cd $REMOTE_ROOT; nohup bash -c 'source .venv/bin/activate && python -u remote/run_alchemi.py $MODE $EXTRA; echo \"exit_code=\$?\"' > $LOG 2>&1 < /dev/null & echo \$! > results/logs/${MODE}_${STAMP}.pid"

# Wait (poll every 30 s) and pull partial results incrementally.
while rssh "kill -0 \$(cat $REMOTE_ROOT/results/logs/${MODE}_${STAMP}.pid) 2>/dev/null"; do
  sleep 30
  rssh "tail -n 2 $REMOTE_ROOT/$LOG" || true
  "$(dirname "$0")/collect_brev_results.sh" >/dev/null || true
done
"$(dirname "$0")/collect_brev_results.sh"
tail -n 5 "$ALCHEMI_DIR/logs/${MODE}_${STAMP}.log"
grep -q '^exit_code=0$' "$ALCHEMI_DIR/logs/${MODE}_${STAMP}.log"
