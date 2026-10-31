#!/usr/bin/env bash
# Runs ON the Brev GPU box. Creates ~/fermentai/.venv (Python 3.12) with CUDA PyTorch and
# NVIDIA ALCHEMI Toolkit (nvalchemi-toolkit) + AIMNet2 + ASE, then records the environment.
# Idempotent: re-running re-uses the venv. Usage: bash ~/fermentai/remote/setup_env.sh
set -euo pipefail
NVALCHEMI_VERSION="${NVALCHEMI_VERSION:-0.2.0}"
CUDA_EXTRA="${CUDA_EXTRA:-cu12}"     # cu12 or cu13; must match the driver (see nvidia-smi)
ROOT="$HOME/fermentai"
ENV_DIR="$ROOT/results/environment"
mkdir -p "$ROOT" "$ENV_DIR"
cd "$ROOT"

if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"
uv --version

if [ ! -x .venv/bin/python ]; then
  uv venv --python 3.12 .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
uv pip install "nvalchemi-toolkit[${CUDA_EXTRA},aimnet,ase]==${NVALCHEMI_VERSION}" pyyaml matplotlib

# The PyPI warp-lang wheel (>=1.10) is built with CUDA Toolkit 13.x and needs an R580+ driver.
# On CUDA-12 drivers (e.g. 570.x / CUDA 12.8 on hyperstack A6000) Warp silently falls back to CPU
# and nvalchemiops kernels fail with "Invalid device identifier: cuda:0". Fix documented by Warp:
# install the matching "+cu12" wheel from GitHub Releases.
DRIVER_MAJOR="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1 | cut -d. -f1)"
if [ "${DRIVER_MAJOR:-0}" -lt 580 ]; then
  WARP_VERSION="$(python -c 'import importlib.metadata as m; print(m.version("warp-lang").split("+")[0])')"
  WARP_URL="https://github.com/NVIDIA/warp/releases/download/v${WARP_VERSION}/warp_lang-${WARP_VERSION}%2Bcu12-py3-none-manylinux_2_28_x86_64.whl"
  echo "driver ${DRIVER_MAJOR} < 580: installing Warp ${WARP_VERSION}+cu12 from ${WARP_URL}"
  uv pip install --force-reinstall --no-deps "warp-lang @ ${WARP_URL}"
fi
python -c "import warp as wp; wp.init(); assert wp.get_cuda_device_count() > 0, 'Warp sees no CUDA device'; print('warp', wp.__version__, 'cuda devices', wp.get_cuda_device_count())"

python - <<'PY'
import torch, importlib.metadata as md
print("torch", torch.__version__, "cuda", torch.version.cuda, "available", torch.cuda.is_available())
if torch.cuda.is_available():
    print("device", torch.cuda.get_device_name(0))
for p in ("nvalchemi-toolkit", "nvalchemi-toolkit-ops", "aimnet", "ase", "warp-lang", "nvidia-physicsnemo"):
    try:
        print(p, md.version(p))
    except md.PackageNotFoundError:
        print(p, "NOT INSTALLED")
PY
uv pip freeze > "$ENV_DIR/pip_freeze.txt"
echo "setup_env.sh: OK"
