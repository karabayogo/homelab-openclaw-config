#!/usr/bin/env bash
set -euo pipefail

OPENCLAW_DIR="$HOME/.openclaw"
REPO_DIR="$HOME/.openclaw-gitops/openclaw"
VERSIONS_FILE="$REPO_DIR/versions.env"
FNM_INSTALL_URL="https://fnm.vercel.app/install"
SOPS_VERSION="v3.13.1"

if [ ! -f "$VERSIONS_FILE" ]; then
  echo "ERROR: $VERSIONS_FILE not found"
  exit 1
fi

# shellcheck disable=SC1090
source "$VERSIONS_FILE"

if [ -z "${NODE_MAJOR:-}" ] || [ -z "${OPENCLAW_NPM_TAG:-}" ] || [ -z "${OLLAMA_MODEL:-}" ]; then
  echo "ERROR: versions.env is missing required keys"
  exit 1
fi

if ! command -v age >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq age >/dev/null
fi

if ! command -v sops >/dev/null 2>&1; then
  sudo curl -fsSL "https://github.com/getsops/sops/releases/download/${SOPS_VERSION}/sops-${SOPS_VERSION}.linux.amd64" -o /usr/local/bin/sops
  sudo chmod +x /usr/local/bin/sops
fi

# Install fnm if missing.
if ! command -v fnm >/dev/null 2>&1; then
  curl -fsSL "$FNM_INSTALL_URL" | bash
fi

# Resolve fnm location.
FNM_BIN="$HOME/.local/share/fnm/fnm"
if [ ! -x "$FNM_BIN" ] && [ -x "$HOME/.fnm/fnm" ]; then
  FNM_BIN="$HOME/.fnm/fnm"
fi
if [ ! -x "$FNM_BIN" ]; then
  echo "ERROR: fnm binary not found"
  exit 1
fi

export PATH="$(dirname "$FNM_BIN"):$PATH"
eval "$($FNM_BIN env)"

# Enforce Node major version.
fnm install "$NODE_MAJOR"
fnm default "$NODE_MAJOR"
fnm use "$NODE_MAJOR"

# Install Docker if missing.
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
sudo systemctl enable docker
sudo systemctl restart docker
sudo usermod -aG docker "$USER" || true

# Ensure OpenClaw runtime directories.
mkdir -p "$OPENCLAW_DIR"
touch "$OPENCLAW_DIR/gateway.systemd.env"
chmod 600 "$OPENCLAW_DIR/gateway.systemd.env"

# Sync desired config files from git.
install -m 600 "$REPO_DIR/openclaw.json" "$OPENCLAW_DIR/openclaw.json"
install -m 600 "$REPO_DIR/.sops.yaml" "$OPENCLAW_DIR/.sops.yaml"
install -m 600 "$REPO_DIR/secrets.json.enc" "$OPENCLAW_DIR/secrets.json.enc"

# Decrypt secrets. Requires AGE key at ~/.config/sops/age/keys.txt.
sops --decrypt "$OPENCLAW_DIR/secrets.json.enc" > "$OPENCLAW_DIR/secrets.json"
chmod 600 "$OPENCLAW_DIR/secrets.json"

# Install/upgrade OpenClaw to desired tag.
npm install -g "openclaw@${OPENCLAW_NPM_TAG}"

# Ensure Ollama container is running with persistent storage.
if ! sudo docker ps --format '{{.Names}}' | awk 'index($0,"ollama")==1{found=1} END{exit found?0:1}'; then
  if sudo docker ps -a --format '{{.Names}}' | awk 'index($0,"ollama")==1{found=1} END{exit found?0:1}'; then
    sudo docker rm -f ollama
  fi
  sudo docker run -d \
    --name ollama \
    --restart unless-stopped \
    -p 11434:11434 \
    -v /data/ollama:/root/.ollama \
    ollama/ollama
fi

# Wait for Ollama API, then ensure embedding model is present.
for i in $(seq 1 30); do
  if curl -sf http://127.0.0.1:11434/api/tags >/dev/null; then
    break
  fi
  sleep 2
done
sudo docker exec ollama ollama pull "$OLLAMA_MODEL"

# Ensure OpenClaw service matches desired Node major and is running.
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/openclaw-gateway.service" <<UNITEOF
[Unit]
Description=OpenClaw Gateway
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/.openclaw
Environment=PATH=%h/.local/share/fnm:/usr/local/bin:/usr/bin:/bin
EnvironmentFile=-%h/.openclaw/gateway.systemd.env
ExecStart=/bin/bash -lc 'export PATH="$HOME/.local/share/fnm:$PATH"; eval "$(fnm env)"; fnm exec --using ${NODE_MAJOR} -- openclaw gateway --port 18789'
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
UNITEOF

systemctl --user daemon-reload
systemctl --user enable --now openclaw-gateway.service
systemctl --user restart openclaw-gateway.service
