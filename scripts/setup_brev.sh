#!/usr/bin/env bash
# Mac-side: verify the Brev instance is reachable, copy remote/ code over, build the remote
# Python env (remote/setup_env.sh) and save the GPU environment report under data/alchemi/environment/.
# Usage: scripts/setup_brev.sh            (instance name from $BREV_INSTANCE, default fermentai-gpu)
# The instance must already exist (create it with `brev create fermentai-gpu --type <type>`).
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
ENV_OUT="$ALCHEMI_DIR/environment"
mkdir -p "$ENV_OUT"

brev ls | tee "$ENV_OUT/brev_ls_setup.txt"
brev refresh >/dev/null
rssh 'echo ssh-ok; hostname; uname -a; cat /etc/os-release | head -4; nproc; free -g | head -2; df -h ~ | tail -1' \
  | tee "$ENV_OUT/remote_host.txt"
rssh 'nvidia-smi; nvidia-smi --query-gpu=name,driver_version,memory.total,compute_cap --format=csv' \
  | tee "$ENV_OUT/nvidia-smi.txt"

rssh "mkdir -p $REMOTE_ROOT/remote $REMOTE_ROOT/configs $REMOTE_ROOT/results/logs"
rsync_to --delete "$REPO_ROOT/remote/" "$BREV_INSTANCE:$REMOTE_ROOT/remote/"
rsync_to "$REPO_ROOT/configs/molecular.yaml" "$BREV_INSTANCE:$REMOTE_ROOT/configs/molecular.yaml"

LOG="logs/setup_env_$(date -u +%Y%m%dT%H%M%SZ).log"
rssh "cd $REMOTE_ROOT && bash remote/setup_env.sh > results/$LOG 2>&1; echo exit=\$? >> results/$LOG; tail -20 results/$LOG"
"$(dirname "$0")/collect_brev_results.sh"
echo "setup_brev.sh: done ($(ts))"
