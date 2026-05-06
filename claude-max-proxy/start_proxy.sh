#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
: "${PORT:=5678}"
: "${UPSTREAM:=https://api.anthropic.com}"
: "${DRY_RUN:=0}"
exec python3 proxy.py
