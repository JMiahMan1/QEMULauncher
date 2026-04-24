#!/bin/bash
set -euo pipefail

MAC_TEST_HOST="${MAC_TEST_HOST:-}"
MAC_TEST_USER="${MAC_TEST_USER:-}"
MAC_TEST_REPO_PATH="${MAC_TEST_REPO_PATH:-}"
MAC_TEST_BRANCH="${MAC_TEST_BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"
MAC_TEST_PYTHON="${MAC_TEST_PYTHON:-python3}"

if [[ -z "$MAC_TEST_HOST" || -z "$MAC_TEST_USER" || -z "$MAC_TEST_REPO_PATH" ]]; then
    echo "Required environment:"
    echo "  MAC_TEST_HOST=<mac-or-dns-name>"
    echo "  MAC_TEST_USER=<macOS username>"
    echo "  MAC_TEST_REPO_PATH=/Users/<user>/Code/QEMULauncher"
    exit 1
fi

REMOTE="${MAC_TEST_USER}@${MAC_TEST_HOST}"

# shellcheck disable=SC2029
ssh "$REMOTE" \
    "MAC_TEST_REPO_PATH=$(printf '%q' "$MAC_TEST_REPO_PATH") MAC_TEST_BRANCH=$(printf '%q' "$MAC_TEST_BRANCH") MAC_TEST_PYTHON=$(printf '%q' "$MAC_TEST_PYTHON") /bin/bash -s" <<'EOF'
set -euo pipefail
cd "$MAC_TEST_REPO_PATH"
git fetch --all --tags
git checkout "$MAC_TEST_BRANCH"
git pull --ff-only || true
$MAC_TEST_PYTHON -m pip install -r requirements.txt
ruff check .
pytest -q
$MAC_TEST_PYTHON qemu_app.py --integrity-check
./build.sh remote-smoke
EOF
