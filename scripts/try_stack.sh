#!/usr/bin/env bash
# try_stack.sh - install the pinned service stack into a THROWAWAY venv and run the two smoke tests.
#
# Settles (docs/specs/api.md): does the stack install and run on this host's Python and CPU, and
# do websockets work. A failed install is itself the answer (e.g. the host's Python is older than
# the SDK's 3.10 floor), so each step is reported and the script carries on to the next.
#
# Touches nothing outside a mktemp directory, which is removed on exit. Needs the network for pip.
# The bound on a hung download is the human's Ctrl-C; pip applies its own retry limits.
#
# The pins are the versions verified TOGETHER on x86_64 / CPython 3.13 (2026-11-16).
set -u
here=$(cd "$(dirname "$0")" && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

echo "host: $(uname -m), $(python3 --version 2>&1)"
fail=0

step() {  # step NAME CMD...
  local name=$1; shift
  if "$@"; then echo "RESULT $name: ok"; else echo "RESULT $name: FAIL (exit $?)"; fail=1; return 1; fi
}

step venv        python3 -m venv "$tmp/venv" || exit 1
step pip-install "$tmp/venv/bin/python" -m pip install --quiet \
    mcp==2.3.0 uvicorn==0.54.0 websockets==17.4 starlette==1.10.0 || { echo "INSTALL FAILED: see pip output above"; exit 1; }
echo "installed:"; "$tmp/venv/bin/python" -m pip list --format=freeze 2>/dev/null | grep -iE '^(mcp|starlette|uvicorn|websockets|pydantic)=='
step smoke-mcp   "$tmp/venv/bin/python" -I "$here/smoke_mcp_dynamic.py"
step smoke-ws    "$tmp/venv/bin/python" -I "$here/smoke_ws.py"
exit $fail
