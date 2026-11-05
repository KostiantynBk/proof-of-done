#!/usr/bin/env bash
# Deploy the current working tree to the VM: rsync-free (tar over ssh), build, restart.
# Usage: deploy/deploy.sh <vm-ip> [ssh-key]
set -euo pipefail
IP="$1"; KEY="${2:-$HOME/.ssh/pod_vultr}"
SSH="ssh -i $KEY -o StrictHostKeyChecking=accept-new root@$IP"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOSTNAME_DASHED="$(echo "$IP" | tr . -).sslip.io"

# Frontend is built locally so the VM needs no Node toolchain.
if [ -d "$ROOT/frontend" ] && [ -f "$ROOT/frontend/package.json" ]; then
  (cd "$ROOT/frontend" && npm run build)
fi

tar -C "$ROOT" -czf - \
  --exclude='.venv*' --exclude='node_modules' --exclude='__pycache__' --exclude='.git' \
  --exclude='data' --exclude='*.pyc' --exclude='frontend/src' \
  pyproject.toml backend sandbox samples deploy frontend/dist docs README.md LICENSE AGENTS.md 2>/dev/null \
  | $SSH "mkdir -p /srv/pod/app && tar -C /srv/pod/app -xzf - && chown -R pod:pod /srv/pod/app"

$SSH bash -s <<REMOTE
set -euo pipefail
cd /srv/pod/app
docker build -q -t pod-sandbox:latest sandbox/ >/dev/null
[ -d .venv ] || sudo -u pod python3.12 -m venv .venv
sudo -u pod .venv/bin/pip install -q -e '.[dev]'
cp deploy/pod.service /etc/systemd/system/pod.service
POD_HOSTNAME=$HOSTNAME_DASHED envsubst < deploy/Caddyfile.tmpl > /etc/caddy/Caddyfile 2>/dev/null || sed "s/{\\$POD_HOSTNAME}/$HOSTNAME_DASHED/" deploy/Caddyfile.tmpl > /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable --now pod.service
systemctl restart pod.service
systemctl reload caddy || systemctl restart caddy
sleep 2
curl -fsS http://127.0.0.1:8000/api/health
REMOTE
echo; echo "Deployed: https://$HOSTNAME_DASHED"
