#!/usr/bin/env bash
# DogBuddyCn installer — venv + deps + data dirs + starter config + service wiring.
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
#
# Layout produced:
#   <repo>/.venv                virtualenv (must sit inside the repo: `manage.py update` uses it)
#   /etc/dogbuddy/config.yaml   configuration (never touched by an upgrade)
#   /var/lib/dogbuddy/          database + memory + logs + checkpoints + workspaces
#
# Offline install: drop a `wheels/` directory next to this script (see
# scripts/make_bundle.py --wheels) and pip switches to --no-index automatically.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${DOGBUDDY_VENV:-$REPO_DIR/.venv}"
CONFIG_DIR="${DOGBUDDY_CONFIG_DIR:-/etc/dogbuddy}"
DATA_DIR="${DOGBUDDY_DATA_DIR:-/var/lib/dogbuddy}"
CONFIG_FILE="$CONFIG_DIR/config.yaml"
SERVICE_USER="${DOGBUDDY_USER:-root}"
PIP_INDEX="${DOGBUDDY_PIP_INDEX:-}"

echo "== DogBuddyCn install =="
echo "   repo:   $REPO_DIR"
echo "   venv:   $VENV_DIR"
echo "   config: $CONFIG_FILE"
echo "   data:   $DATA_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 is required (3.11+)" >&2
  exit 1
fi

PYVER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
echo "   python: $PYVER"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || {
  echo "ERROR: Python 3.11+ required, found $PYVER" >&2
  exit 1
}

if [ ! -x "$VENV_DIR/bin/python" ]; then
  echo "-- creating venv"
  python3 -m venv "$VENV_DIR"
fi

PIP_ARGS=(-r "$REPO_DIR/requirements.txt" --disable-pip-version-check)
if [ -f "$REPO_DIR/constraints.txt" ]; then PIP_ARGS+=(-c "$REPO_DIR/constraints.txt"); fi
if [ -d "$REPO_DIR/wheels" ]; then
  echo "-- offline install from $REPO_DIR/wheels"
  PIP_ARGS+=(--no-index --find-links "$REPO_DIR/wheels")
elif [ -n "$PIP_INDEX" ]; then
  PIP_ARGS+=(-i "$PIP_INDEX")
fi

echo "-- installing dependencies (this can take a while)"
"$VENV_DIR/bin/python" -m pip install --upgrade pip -q --disable-pip-version-check || true
"$VENV_DIR/bin/python" -m pip install "${PIP_ARGS[@]}" -q

echo "-- creating directories"
mkdir -p "$CONFIG_DIR" "$DATA_DIR"/memory/windows "$DATA_DIR"/logs "$DATA_DIR"/checkpoints "$DATA_DIR"/workspaces "$DATA_DIR"/skills
chmod 700 "$CONFIG_DIR" "$DATA_DIR" 2>/dev/null || true

if [ ! -f "$CONFIG_FILE" ]; then
  echo "-- writing starter config $CONFIG_FILE"
  sed -e "s|^  data_dir:.*|  data_dir: $DATA_DIR|" \
      -e "s|^  workspaces_dir:.*|  workspaces_dir: $DATA_DIR/workspaces|" \
      "$REPO_DIR/config.example.yaml" > "$CONFIG_FILE"
  chmod 600 "$CONFIG_FILE" 2>/dev/null || true
fi

# deployment marker (not the version — see dogbuddy/__init__.py)
STAMP="$(git -C "$REPO_DIR" describe --tags --always --dirty 2>/dev/null || echo "source-snapshot")"
printf '%s\n' "$STAMP" > "$REPO_DIR/VERSION"
echo "-- build stamp: $STAMP"

echo "-- smoke test"
"$VENV_DIR/bin/python" -c "import dogbuddy, fastapi, yaml, httpx, croniter; print('   import ok, version', dogbuddy.__version__)"

export DOGBUDDY_CONFIG="$CONFIG_FILE"

if [ -d /run/systemd/system ]; then
  echo "-- installing systemd unit dogbuddy.service"
  sed -e "s|@REPO_DIR@|$REPO_DIR|g" -e "s|@VENV_DIR@|$VENV_DIR|g" \
      -e "s|@DATA_DIR@|$DATA_DIR|g" -e "s|@USER@|$SERVICE_USER|g" \
      "$REPO_DIR/systemd/dogbuddy.service" > /etc/systemd/system/dogbuddy.service
  systemctl daemon-reload
  echo "   enable + start:  systemctl enable --now dogbuddy"
else
  echo "-- no systemd detected (container / PRoot)"
  echo "   start manually :  $VENV_DIR/bin/python $REPO_DIR/manage.py start"
  echo "   or register as an AegisServerPanel managed app (recommended here):"
  echo "     python3 $REPO_DIR/scripts/register_panel_app.py \\"
  echo "         --panel http://127.0.0.1:8787 --app-dir $REPO_DIR --config $CONFIG_FILE"
fi

echo "== install done =="
echo "   version : $("$VENV_DIR/bin/python" -c 'import dogbuddy; print(dogbuddy.__version__)')"
echo "   health  : curl -s http://127.0.0.1:8100/healthz"
echo "   upgrade : $VENV_DIR/bin/python $REPO_DIR/manage.py update --check"
echo "             $VENV_DIR/bin/python $REPO_DIR/manage.py update --from-bundle <pkg>"
