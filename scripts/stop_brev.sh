#!/usr/bin/env bash
# Mac-side: pull results one last time, then stop the instance. If the provider does not support
# stop (e.g. hyperstack types show no "S" feature in `brev search`), pass --delete-if-unstoppable
# to delete it instead -- only after results are safely on the Mac.
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
"$(dirname "$0")/collect_brev_results.sh"
mkdir -p "$ALCHEMI_DIR/environment"
if brev stop "$BREV_INSTANCE" 2>&1 | tee -a "$ALCHEMI_DIR/environment/brev_stop.log"; then
  :
fi
sleep 5
brev ls | tee -a "$ALCHEMI_DIR/environment/brev_stop.log"
if [ "${1:-}" = "--delete-if-unstoppable" ] && brev ls | grep -q "^ *$BREV_INSTANCE .*RUNNING"; then
  echo "instance still RUNNING after stop; deleting ($(ts))" | tee -a "$ALCHEMI_DIR/environment/brev_stop.log"
  brev delete "$BREV_INSTANCE" 2>&1 | tee -a "$ALCHEMI_DIR/environment/brev_stop.log"
fi
echo "stop_brev.sh finished at $(ts)" | tee -a "$ALCHEMI_DIR/environment/brev_stop.log"
