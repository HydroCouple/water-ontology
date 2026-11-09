# source ./activate.sh  — puts the venv on PATH and keeps all caches inside the repo
export REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export UV_CACHE_DIR="$REPO_ROOT/.uv-cache"
export TMPDIR="$REPO_ROOT/.tmp"
export PYSTOW_HOME="$REPO_ROOT/.pystow"
export PYTHONPATH="$REPO_ROOT"
export PATH="$REPO_ROOT/.venv/bin:$PATH"
mkdir -p "$UV_CACHE_DIR" "$TMPDIR" "$PYSTOW_HOME"
