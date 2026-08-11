#!/usr/bin/env bash
# ============================================================================
#  AI Electrician — Ubuntu / Debian installer
#
#  Installs Docker (if missing), generates a secure .env, then builds and
#  starts the full stack (db, redis, api, worker, web).
#
#  Usage:
#     sudo ./scripts/install-ubuntu.sh
#
#  Re-running is safe: an existing .env is never overwritten, so your
#  database password and secrets stay stable across upgrades.
# ============================================================================
set -euo pipefail

# ---- pretty output --------------------------------------------------------
c_reset="\033[0m"; c_bold="\033[1m"; c_green="\033[32m"; c_yellow="\033[33m"; c_red="\033[31m"; c_blue="\033[36m"
say()  { echo -e "${c_blue}▶${c_reset} $*"; }
ok()   { echo -e "${c_green}✓${c_reset} $*"; }
warn() { echo -e "${c_yellow}!${c_reset} $*"; }
die()  { echo -e "${c_red}✗ $*${c_reset}" >&2; exit 1; }

# ---- resolve repo root (script lives in scripts/) -------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

echo -e "${c_bold}⚡ AI Electrician — Ubuntu installer${c_reset}"
echo "Repository: $REPO_ROOT"
echo

# ---- must be root (for Docker install + service management) ---------------
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  if command -v sudo >/dev/null 2>&1; then SUDO="sudo"; else die "Please run as root (sudo ./scripts/install-ubuntu.sh)"; fi
fi

# ---- helper: is this an interactive terminal? -----------------------------
is_tty() { [ -t 0 ]; }
ask() { # ask "prompt" "default" -> echoes answer
  local prompt="$1" def="$2" ans
  if is_tty; then read -r -p "$prompt [$def]: " ans || true; echo "${ans:-$def}"; else echo "$def"; fi
}
ask_yesno() { # ask_yesno "prompt" "y|n" -> returns 0 for yes
  local prompt="$1" def="$2" ans
  ans="$(ask "$prompt (y/n)" "$def")"
  [[ "$ans" =~ ^[Yy] ]]
}

# ---- 1. install Docker if needed ------------------------------------------
install_docker() {
  say "Installing Docker Engine + Compose plugin ..."
  $SUDO apt-get update -y
  $SUDO apt-get install -y ca-certificates curl gnupg
  $SUDO install -m 0755 -d /etc/apt/keyrings
  if [ ! -f /etc/apt/keyrings/docker.asc ]; then
    $SUDO curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    $SUDO chmod a+r /etc/apt/keyrings/docker.asc
  fi
  local arch codename
  arch="$(dpkg --print-architecture)"
  codename="$(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")"
  echo "deb [arch=${arch} signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${codename} stable" \
    | $SUDO tee /etc/apt/sources.list.d/docker.list > /dev/null
  $SUDO apt-get update -y
  $SUDO apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  $SUDO systemctl enable --now docker
  ok "Docker installed."
}

if command -v docker >/dev/null 2>&1; then
  ok "Docker already installed ($(docker --version))."
else
  warn "Docker not found."
  install_docker
fi

# ---- 2. determine the compose command -------------------------------------
if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="docker-compose"
else
  die "Docker Compose is not available. Install the docker-compose-plugin and re-run."
fi
ok "Using compose command: $COMPOSE"

# add invoking user to docker group for convenience (takes effect next login)
if [ -n "${SUDO_USER:-}" ] && ! id -nG "$SUDO_USER" | grep -qw docker; then
  $SUDO usermod -aG docker "$SUDO_USER" || true
  warn "Added '$SUDO_USER' to the 'docker' group — log out/in for it to take effect."
fi

# ---- 3. generate .env (only if absent) ------------------------------------
gen_secret() {
  if command -v openssl >/dev/null 2>&1; then openssl rand -hex 32
  else head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; fi
}

if [ -f .env ]; then
  ok ".env already exists — keeping it (edit it manually to change settings)."
else
  say "Creating .env ..."
  cp .env.example .env

  OLLAMA_URL="$(ask 'Ollama server URL' 'http://192.168.203.100:11434')"
  DB_PASS="$(gen_secret)"
  SECRET="$(gen_secret)"

  # replace values in-place
  sed -i "s|^OLLAMA_BASE_URL=.*|OLLAMA_BASE_URL=${OLLAMA_URL}|" .env
  sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${DB_PASS}|" .env
  sed -i "s|^AUTH_SECRET=.*|AUTH_SECRET=${SECRET}|" .env

  if ask_yesno "Require a shared password to open the app (LAN auth)?" "n"; then
    APP_PASS="$(ask 'Choose an app password' 'changeme')"
    sed -i "s|^AUTH_ENABLED=.*|AUTH_ENABLED=true|" .env
    sed -i "s|^AUTH_SHARED_PASSWORD=.*|AUTH_SHARED_PASSWORD=${APP_PASS}|" .env
    ok "LAN auth enabled."
  fi
  ok ".env created with a random DB password and secret."
fi

# ---- 4. build + start ------------------------------------------------------
say "Building and starting the stack (this can take a few minutes the first time) ..."
$SUDO $COMPOSE up -d --build
ok "Containers started."

# ---- 5. wait for the API to be healthy ------------------------------------
API_PORT="$(grep -E '^API_PORT=' .env | cut -d= -f2 || true)"; API_PORT="${API_PORT:-8000}"
WEB_PORT="$(grep -E '^WEB_PORT=' .env | cut -d= -f2 || true)"; WEB_PORT="${WEB_PORT:-8080}"

say "Waiting for the API to come up ..."
for i in $(seq 1 60); do
  if curl -fsS "http://localhost:${API_PORT}/api/health" >/dev/null 2>&1; then ok "API is healthy."; break; fi
  sleep 2
  [ "$i" -eq 60 ] && warn "API did not report healthy yet — check: $COMPOSE logs api"
done

# ---- 6. check Ollama connectivity -----------------------------------------
say "Checking Ollama connectivity ..."
if curl -fsS "http://localhost:${API_PORT}/api/ollama/status" 2>/dev/null | grep -q '"reachable": true\|"reachable":true'; then
  ok "Ollama is reachable from the API container."
else
  warn "Ollama is NOT reachable yet."
  echo "   • Confirm the server at your OLLAMA_BASE_URL is running and reachable from this host."
  echo "   • Details: curl http://localhost:${API_PORT}/api/ollama/status"
fi

# ---- done ------------------------------------------------------------------
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"; IP="${IP:-<server-ip>}"
echo
echo -e "${c_bold}${c_green}Installation complete.${c_reset}"
echo -e "  Web app:      ${c_bold}http://${IP}:${WEB_PORT}${c_reset}"
echo -e "  API / docs:   http://${IP}:${API_PORT}/docs"
echo -e "  Ollama check: http://${IP}:${API_PORT}/api/ollama/status"
echo
echo "Useful commands:"
echo "  $COMPOSE logs -f api worker     # view logs"
echo "  $COMPOSE restart api worker     # restart services"
echo "  $COMPOSE down                   # stop the stack"
echo "  ./scripts/backup.sh             # back up DB + prints"
