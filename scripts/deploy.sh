#!/bin/bash
# Deploy the winuse agent to the Windows PC over SSH. Safe to re-run.
# The target host is WINUSE_SSH_HOST, or the host= line in winuse.conf (`winuse config show`).
#   scripts/deploy.sh            copy code; restart the agent if its scheduled task exists
#   scripts/deploy.sh --install  first time: also create C:\winuse, the venv, and pip-install the requirements
#   scripts/deploy.sh --task     register the 'winuse' logon scheduled task (the step that makes it auto-start)
set -euo pipefail
cd "$(dirname "$0")/.."
HOST="${WINUSE_SSH_HOST:-$(sed -n 's/^WINUSE_SSH_HOST=//p' winuse.conf 2>/dev/null | head -1)}"
if [ -z "$HOST" ]; then
  echo "No Windows host set. Run: bin/winuse config set host <ssh-host-name>  (or set WINUSE_SSH_HOST)" >&2
  exit 2
fi
MODE="${1:-}"
S() { ssh -q -o BatchMode=yes -o ConnectTimeout=8 "$HOST" "$@"; }

if [ ! -s .token ]; then
  python3 -c "import secrets; print(secrets.token_urlsafe(32))" > .token
  chmod 600 .token
  echo "generated a new token in .token"
fi

if [ "$MODE" = "--install" ]; then
  S 'if not exist C:\winuse\agent mkdir C:\winuse\agent'
  S 'if not exist C:\winuse\logs mkdir C:\winuse\logs'
  S 'if not exist C:\winuse\venv\Scripts\python.exe python -m venv C:\winuse\venv'
fi

scp -q agent/*.py agent/*.ps1 "$HOST:C:/winuse/agent/"
scp -q scripts/install-task.ps1 agent/requirements.txt "$HOST:C:/winuse/"
scp -q .token "$HOST:C:/winuse/token.txt"
# token readable by Administrators and SYSTEM only
S 'icacls C:\winuse\token.txt /inheritance:r /grant:r *S-1-5-32-544:F *S-1-5-18:F' > /dev/null
echo "copied agent code to $HOST:C:\\winuse"

if [ "$MODE" = "--install" ]; then
  S 'C:\winuse\venv\Scripts\python.exe -m pip install --disable-pip-version-check -q -r C:\winuse\requirements.txt'
  S 'C:\winuse\venv\Scripts\python.exe -m pip list --disable-pip-version-check'
fi

if [ "$MODE" = "--task" ]; then
  S 'powershell -NoProfile -ExecutionPolicy Bypass -File C:\winuse\install-task.ps1'
fi

if S 'schtasks /query /tn winuse' > /dev/null 2>&1; then
  S 'schtasks /end /tn winuse' > /dev/null 2>&1 || true
  sleep 1
  S 'schtasks /run /tn winuse' > /dev/null
  sleep 2
  bin/winuse status
else
  echo "the 'winuse' scheduled task is not registered yet, so the agent is not running."
  echo "register it with: scripts/deploy.sh --task"
fi
