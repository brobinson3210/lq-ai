#!/bin/bash
# Run the connector's end-to-end test suite against the live LQ.AI stack.
#
#   ./run-tests.sh          # readable PASS/FAIL report
#   ./run-tests.sh --json   # one JSON object (used by the mcp-tests.html page)
#
# Uses the same Keychain entry as run.sh and runs inside the lqai-gates
# container's .venv-linux (the host has no usable Python 3.10+). Every
# test record is tagged ZZ-TEST and deleted afterwards.
set -euo pipefail

EMAIL="${LQ_MGMT_EMAIL:-admin@lq.ai}"
PW="$(security find-generic-password -s lq-ai-management -a "$EMAIL" -w)"
DOCKER=/Applications/Docker.app/Contents/Resources/bin/docker
command -v docker >/dev/null 2>&1 && DOCKER=docker

"$DOCKER" start lqai-gates >/dev/null
exec "$DOCKER" exec \
  -e LQ_MGMT_EMAIL="$EMAIL" -e LQ_MGMT_PASSWORD="$PW" \
  -e LQ_MGMT_BASE_URL="${LQ_MGMT_BASE_URL:-http://host.docker.internal:8000/api/v1}" \
  lqai-gates /r/management-mcp/.venv-linux/bin/python /r/management-mcp/tests/test_connector.py "$@" 2>/dev/null
