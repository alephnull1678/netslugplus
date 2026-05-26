#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")"

if [ -f relay_secret.txt ] && [ "${1:-}" != "--force" ]; then
    echo "relay_secret.txt already exists."
    echo "Run './generate_secret.sh --force' to replace it."
    exit 1
fi

python3 - <<'PY'
import secrets
from pathlib import Path

Path("relay_secret.txt").write_text(secrets.token_urlsafe(32), encoding="ascii")
print("Generated relay_secret.txt.")
PY
