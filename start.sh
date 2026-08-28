#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"

if [ -f ./.env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

exec python3 server.py
