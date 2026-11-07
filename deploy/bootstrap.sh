#!/usr/bin/env bash
# One-time host setup for a fresh Ubuntu 24.04 Vultr VM (run as root).
# Installs Docker + gVisor (runsc), a size-capped tmpfs for sandbox workspaces,
# Caddy (automatic HTTPS), Python 3.12 venv deps, and a dedicated `pod` user.
set -euo pipefail

POD_HOME=/srv/pod
WORK_TMPFS_SIZE="${WORK_TMPFS_SIZE:-6g}"

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y ca-certificates curl gnupg ufw python3.12-venv python3-pip git jq

# --- Docker Engine -----------------------------------------------------------
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
# --- gVisor ------------------------------------------------------------------
curl -fsSL https://gvisor.dev/archive.key | gpg --batch --yes --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main" \
  > /etc/apt/sources.list.d/gvisor.list
# --- Caddy -------------------------------------------------------------------
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list

apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io runsc caddy

# Register runsc with Docker; bound container logs globally.
cat > /etc/docker/daemon.json <<'JSON'
{
  "runtimes": { "runsc": { "path": "/usr/bin/runsc" } },
  "log-driver": "json-file",
  "log-opts": { "max-size": "1m", "max-file": "1" }
}
JSON
systemctl restart docker

# --- Service user and directories ----------------------------------------------
# The sandbox must use exactly the service's numeric IDs for safe cleanup.
# Never repurpose an existing account; select a free matching UID/GID.
if ! id pod >/dev/null 2>&1; then
  if getent group pod >/dev/null; then echo "group pod already exists without user pod" >&2; exit 1; fi
  uid=1000
  while getent passwd "$uid" >/dev/null || getent group "$uid" >/dev/null; do uid=$((uid + 1)); done
  groupadd -g "$uid" pod
  useradd -u "$uid" -g "$uid" --create-home --home-dir "$POD_HOME" --shell /usr/sbin/nologin pod
fi
uid=$(id -u pod); gid=$(id -g pod)
if [ "$uid" -eq 0 ] || [ "$gid" -eq 0 ] || [ "$(getent group "$gid" | cut -d: -f1)" != pod ]; then
  echo "invalid pod identity" >&2; exit 1
fi
usermod -aG docker pod
mkdir -p "$POD_HOME/app" "$POD_HOME/data" "$POD_HOME/work" /etc/pod
# Size-capped tmpfs for sandbox workspaces: a fill-the-disk attack hits this cap, not the host disk.
if ! grep -q " $POD_HOME/work " /etc/fstab; then
  echo "tmpfs $POD_HOME/work tmpfs rw,nosuid,nodev,size=$WORK_TMPFS_SIZE,nr_inodes=400k,mode=0750,uid=$(id -u pod),gid=$(id -g pod) 0 0" >> /etc/fstab
fi
mountpoint -q "$POD_HOME/work" || mount "$POD_HOME/work"
chown -R pod:pod "$POD_HOME"
touch /etc/pod/pod.env && chown root:pod /etc/pod/pod.env && chmod 0640 /etc/pod/pod.env
printf 'POD_SANDBOX_UID=%s\nPOD_SANDBOX_GID=%s\n' "$uid" "$gid" > /etc/pod/identity.env
chown root:root /etc/pod/identity.env && chmod 0644 /etc/pod/identity.env

# --- Firewall ------------------------------------------------------------------
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "bootstrap done: docker $(docker --version | cut -d' ' -f3) runsc $(runsc --version | head -1)"
