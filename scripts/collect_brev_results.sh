#!/usr/bin/env bash
# Mac-side, pull-only: copy everything under ~/fermentai/results on the Brev box into
# data/alchemi/ (never deletes local files). Safe to run repeatedly / incrementally.
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
mkdir -p "$ALCHEMI_DIR"
rsync_to --exclude '*.pt' "$BREV_INSTANCE:$REMOTE_ROOT/results/" "$ALCHEMI_DIR/"
echo "collected $BREV_INSTANCE:$REMOTE_ROOT/results/ -> $ALCHEMI_DIR ($(ts))"
