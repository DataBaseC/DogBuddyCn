#!/usr/bin/env bash
# DogBuddyCn installer — venv + deps + data dirs + systemd unit + starter config.
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${DOGBUDDY_DATA_DIR:-/etc/dogbuddy}"
VENV_DIR="${DOGBUDDY_VENV:-$DATA_DIR/venv}"
SERVICE_USER="${DOGBUDDY_USER:-root}"

echo "== DogBuddyCn install =="
echo "   repo:   $REPO_DIR"
echo "   data:   $DATA_DIR"

if ! command -v python3 >/dev/null; then
  echo "python3 required (3.11+)" >&2; exit 1
fi
PYVER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "   python: $PYVER"

echo "-- creating venv at $VENV_DIR"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -r "$REPO_DIR/requirements.txt" -q

echo "-- creating data dirs"
mkdir -p "$DATA_DIR"/{memory/windows,logs,checkpoints,workspaces,skills}

if [ ! -f "$DATA_DIR/config.yaml" ]; then
  cp "$REPO_DIR/config.example.yaml" "$DATA_DIR/config.yaml"
  echo "-- starter config written to $DATA_DIR/config.yaml (no need to edit now)"
fi

if command -v systemctl >/dev/null && [ -d /etc/systemd/system ]; then
  echo "-- installing systemd unit"
  sed -e "s|@REPO_DIR@|$REPO_DIR|g" -e "s|@VENV_DIR@|$VENV_DIR|g" \
      -e "s|@DATA_DIR@|$DATA_DIR|g" -e "s|@USER@|$SERVICE_USER|g" \
      "$REPO_DIR/systemd/dogbuddy.service" > /etc/systemd/system/dogbuddy.service
  systemctl daemon-reload
  echo "   enable+start with: systemctl enable --now dogbuddy"
else
  echo "-- systemd not found; run manually:"
  echo "   $VENV_DIR/bin/python $REPO_DIR/manage.py start"
fi

echo "== done =="
echo "Next steps:"
echo "  1. start: systemctl start dogbuddy   (or manage.py start)"
echo "  2. open the console http://<server>:8100 — the setup wizard will guide you"
echo "     through the LLM config (base_url + api_key, model list is fetched"
echo "     automatically), panel token and QQ whitelist. No config editing needed."
echo "  3. optional: edit $DATA_DIR/config.yaml later for gateway/panel tokens"
