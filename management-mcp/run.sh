#!/bin/bash
# Launcher for the LQ.AI Management-tab MCP connector.
#
# Registered with Claude Code as the MCP server command. It:
#   1. fetches the LQ.AI password from the macOS Keychain
#      (service "lq-ai-management") — credentials never live in a file;
#   2. finds a working Python 3.10+ with the `mcp` package (bootstrapping
#      a local .venv on first run);
#   3. execs server.py speaking MCP over stdio.
#
# Fallback: if no host Python works (e.g. the ~/.local toolchain is
# broken), it runs the server inside the `lqai-gates` Docker container,
# which mounts this repo at /r.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EMAIL="${LQ_MGMT_EMAIL:-admin@lq.ai}"
export LQ_MGMT_EMAIL="$EMAIL"

# --- 1. Credentials from the Keychain (unless already provided) -------------
if [ -z "${LQ_MGMT_PASSWORD:-}" ]; then
  if ! LQ_MGMT_PASSWORD="$(security find-generic-password -s lq-ai-management -a "$EMAIL" -w 2>/dev/null)"; then
    echo "error: no Keychain entry. Create it once with:" >&2
    echo "  security add-generic-password -s lq-ai-management -a $EMAIL -w '<your LQ.AI password>'" >&2
    exit 1
  fi
  export LQ_MGMT_PASSWORD
fi

# --- 2. Find a Python that can run the server -------------------------------
# mcp is pinned <2: FastMCP lives at mcp.server.fastmcp in the 1.x SDK.
MCP_PIN='mcp>=1.2,<2'
works() { "$1" -c 'import sys; assert sys.version_info >= (3, 10); import mcp.server.fastmcp, httpx' >/dev/null 2>&1; }

VENV="$DIR/.venv"
if [ -x "$VENV/bin/python" ] && works "$VENV/bin/python"; then
  exec "$VENV/bin/python" "$DIR/server.py"
fi

# Bootstrap a venv from any usable 3.10+ interpreter.
for base in "${LQ_MGMT_PYTHON:-}" /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 \
            /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
  [ -n "$base" ] || continue
  command -v "$base" >/dev/null 2>&1 || continue
  "$base" -c 'import sys; assert sys.version_info >= (3, 10)' >/dev/null 2>&1 || continue
  echo "bootstrapping venv with $base ..." >&2
  if "$base" -m venv "$VENV" >&2 && "$VENV/bin/pip" install --quiet "$MCP_PIN" httpx >&2 \
     && works "$VENV/bin/python"; then
    exec "$VENV/bin/python" "$DIR/server.py"
  fi
  rm -rf "$VENV"
done

# --- 3. Fallback: run inside the lqai-gates container ------------------------
# Deps go in a container-side venv (.venv-linux on the repo mount) so the
# connector NEVER touches the container's global site-packages — those
# belong to the api test rig (fastapi pins starlette<0.49; installing mcp
# globally would upgrade starlette over the pin and break the gates).
DOCKER=/Applications/Docker.app/Contents/Resources/bin/docker
command -v docker >/dev/null 2>&1 && DOCKER=docker
CVENV=/r/management-mcp/.venv-linux
if "$DOCKER" start lqai-gates >/dev/null 2>&1; then
  "$DOCKER" exec lqai-gates sh -c \
    "[ -x $CVENV/bin/python ] && $CVENV/bin/python -c 'import mcp.server.fastmcp, httpx'" >/dev/null 2>&1 \
    || "$DOCKER" exec lqai-gates sh -c \
       "rm -rf $CVENV && python -m venv $CVENV && $CVENV/bin/pip install --quiet '$MCP_PIN' httpx" >&2
  exec "$DOCKER" exec -i \
    -e LQ_MGMT_EMAIL -e LQ_MGMT_PASSWORD \
    -e LQ_MGMT_BASE_URL="${LQ_MGMT_BASE_URL:-http://host.docker.internal:8000/api/v1}" \
    lqai-gates "$CVENV/bin/python" /r/management-mcp/server.py
fi

echo "error: no working Python 3.10+ found and the lqai-gates container is unavailable." >&2
exit 1
