#!/bin/bash
set -euo pipefail

ENV_FILE="${ENV_FILE:-.env}"

if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

MAC_TEST_HOST="${MAC_TEST_HOST:-}"
MAC_TEST_USER="${MAC_TEST_USER:-}"
MAC_TEST_REPO_PATH="${MAC_TEST_REPO_PATH:-}"
MAC_TEST_BRANCH="${MAC_TEST_BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"
MAC_TEST_PYTHON="${MAC_TEST_PYTHON:-python3.12}"
MAC_TEST_SSH_KEY="${MAC_TEST_SSH_KEY:-}"

if [[ -z "$MAC_TEST_HOST" || -z "$MAC_TEST_USER" || -z "$MAC_TEST_REPO_PATH" ]]; then
    echo "Required values are missing."
    echo "Set them in $ENV_FILE or export them in the shell:"
    echo "  MAC_TEST_HOST=<mac-or-dns-name>"
    echo "  MAC_TEST_USER=<macOS username>"
    echo "  MAC_TEST_REPO_PATH=/Users/<user>/Code/QEMULauncher"
    exit 1
fi

REMOTE="${MAC_TEST_USER}@${MAC_TEST_HOST}"
SSH_ARGS=()

if [[ -n "$MAC_TEST_SSH_KEY" ]]; then
    SSH_ARGS+=(-i "$MAC_TEST_SSH_KEY")
fi

# shellcheck disable=SC2029
ssh "${SSH_ARGS[@]}" "$REMOTE" \
    "MAC_TEST_REPO_PATH=$(printf '%q' "$MAC_TEST_REPO_PATH") MAC_TEST_BRANCH=$(printf '%q' "$MAC_TEST_BRANCH") MAC_TEST_PYTHON=$(printf '%q' "$MAC_TEST_PYTHON") /bin/bash -s" <<'EOF'
set -euo pipefail
cd "$MAC_TEST_REPO_PATH"
git fetch --all --tags
git checkout "$MAC_TEST_BRANCH"
git pull --ff-only || true
$MAC_TEST_PYTHON - <<'PY'
import sys

if sys.version_info < (3, 12):
    raise SystemExit(f"Python 3.12+ is required on the remote Mac. Found {sys.version.split()[0]}.")
PY
SMOKE_VENV="$HOME/.cache/qemu-launcher-mac-smoke-venv"
mkdir -p "$(dirname "$SMOKE_VENV")"
$MAC_TEST_PYTHON -m venv "$SMOKE_VENV"
"$SMOKE_VENV/bin/python" -m pip install --upgrade pip
"$SMOKE_VENV/bin/python" -m pip install -r requirements.txt
"$SMOKE_VENV/bin/python" -m ruff check .
"$SMOKE_VENV/bin/python" -m pytest -q
"$SMOKE_VENV/bin/python" qemu_app.py --integrity-check
./build.sh remote-smoke
EOF
