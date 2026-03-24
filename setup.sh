#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────────────
# setup.sh — One-command setup for Claude Code + GitHub Copilot proxy
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/datorresb/ghcp-cc-proxy/main/setup.sh | bash
#   # or locally:
#   ./setup.sh
# ─────────────────────────────────────────────────────────────────────

PORT="${PORT:-8080}"
PROXY_URL="http://localhost:${PORT}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${BLUE}[setup]${NC} $*"; }
ok()    { echo -e "${GREEN}[setup]${NC} $*"; }
warn()  { echo -e "${YELLOW}[setup]${NC} $*"; }
fail()  { echo -e "${RED}[setup]${NC} $*"; exit 1; }

# ── 1. Check prerequisites ──────────────────────────────────────────

info "Checking prerequisites..."

command -v python3 >/dev/null 2>&1 || fail "python3 not found. Install Python 3.8+."
command -v gh >/dev/null 2>&1      || fail "gh CLI not found. Install: https://cli.github.com"

# Verify gh is authenticated
if ! gh auth token -h github.com >/dev/null 2>&1; then
    warn "GitHub CLI not authenticated. Starting login..."
    gh auth login -h github.com -p https -w
fi
ok "gh CLI authenticated"

# Verify Copilot access by trying to get a token
if ! python3 -c "
import subprocess, json
from urllib.request import Request, urlopen
t = subprocess.check_output(['gh','auth','token','-h','github.com'], text=True).strip()
r = Request('https://api.github.com/copilot_internal/v2/token',
    headers={'Authorization': f'token {t}', 'Editor-Version': 'vscode/1.96.0',
             'Editor-Plugin-Version': 'copilot-chat/0.40.0'})
urlopen(r, timeout=10)
" 2>/dev/null; then
    fail "Cannot get Copilot token. Ensure you have a GitHub Copilot subscription."
fi
ok "GitHub Copilot access verified"

# ── 2. Install Claude Code ──────────────────────────────────────────

if command -v claude >/dev/null 2>&1; then
    ok "Claude Code already installed: $(claude --version 2>/dev/null || echo 'unknown version')"
else
    info "Installing Claude Code via npm..."
    if command -v npm >/dev/null 2>&1; then
        npm install -g @anthropic-ai/claude-code
        ok "Claude Code installed"
    else
        fail "npm not found. Install Node.js 18+ first: https://nodejs.org"
    fi
fi

# ── 3. Drop proxy script ────────────────────────────────────────────

PROXY_SCRIPT="${SCRIPT_DIR}/copilot_proxy.py"

if [ ! -f "$PROXY_SCRIPT" ]; then
    info "Downloading proxy script..."
    curl -fsSL "https://raw.githubusercontent.com/datorresb/ghcp-cc-proxy/main/copilot_proxy.py" \
        -o "$PROXY_SCRIPT"
    chmod +x "$PROXY_SCRIPT"
    ok "Proxy script downloaded to ${PROXY_SCRIPT}"
else
    ok "Proxy script found at ${PROXY_SCRIPT}"
fi

# ── 4. Start proxy ──────────────────────────────────────────────────

# Kill any existing proxy on this port
if lsof -ti:"${PORT}" >/dev/null 2>&1; then
    warn "Port ${PORT} in use — killing existing process"
    kill "$(lsof -ti:"${PORT}")" 2>/dev/null || true
    sleep 1
fi

info "Starting proxy on port ${PORT}..."
PORT="${PORT}" nohup python3 "$PROXY_SCRIPT" > /tmp/copilot-proxy.log 2>&1 &
PROXY_PID=$!

# Wait for proxy to be ready
for i in $(seq 1 10); do
    if curl -sf "${PROXY_URL}/health" >/dev/null 2>&1; then
        ok "Proxy running (PID ${PROXY_PID})"
        break
    fi
    if [ "$i" -eq 10 ]; then
        fail "Proxy failed to start. Check /tmp/copilot-proxy.log"
    fi
    sleep 1
done

# ── 5. Configure Claude Code ────────────────────────────────────────

# Project-level config lives in .claude/settings.json in the repo.
# If running from within the project, that config is already in place.
# We also write global ~/.claude/settings.json as a fallback for users
# running Claude Code from outside the project directory.

info "Configuring Claude Code to use proxy..."

# Check if project-level config exists (running from within the repo)
PROJECT_CLAUDE_CONFIG="${SCRIPT_DIR}/.claude/settings.json"
if [ -f "$PROJECT_CLAUDE_CONFIG" ]; then
    ok "Project-level Claude Code config found at .claude/settings.json"
fi

# Write global config as fallback
CLAUDE_CONFIG_DIR="${HOME}/.claude"
mkdir -p "$CLAUDE_CONFIG_DIR"

SETTINGS_FILE="${CLAUDE_CONFIG_DIR}/settings.json"
if [ -f "$SETTINGS_FILE" ]; then
    # Merge into existing settings using python
    python3 -c "
import json, sys
with open('$SETTINGS_FILE') as f:
    settings = json.load(f)
settings.setdefault('env', {})
settings['env']['ANTHROPIC_BASE_URL'] = '${PROXY_URL}'
settings['env']['ANTHROPIC_AUTH_TOKEN'] = 'sk-copilot-proxy'
settings['env']['DISABLE_NON_ESSENTIAL_MODEL_CALLS'] = '1'
settings['env']['CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC'] = '1'
with open('$SETTINGS_FILE', 'w') as f:
    json.dump(settings, f, indent=2)
" 2>/dev/null || true
else
    cat > "$SETTINGS_FILE" <<SETTINGS
{
  "env": {
    "ANTHROPIC_BASE_URL": "${PROXY_URL}",
    "ANTHROPIC_AUTH_TOKEN": "sk-copilot-proxy",
    "DISABLE_NON_ESSENTIAL_MODEL_CALLS": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"
  }
}
SETTINGS
fi
ok "Global Claude Code config updated → ANTHROPIC_BASE_URL=${PROXY_URL}"

# ── 6. Summary ──────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║          Setup complete!                                ║${NC}"
echo -e "${GREEN}╠══════════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║${NC}  Proxy:       ${PROXY_URL}                        ${GREEN}║${NC}"
echo -e "${GREEN}║${NC}  Proxy PID:   ${PROXY_PID}                                     ${GREEN}║${NC}"
echo -e "${GREEN}║${NC}  Proxy log:   /tmp/copilot-proxy.log                ${GREEN}║${NC}"
echo -e "${GREEN}║${NC}  Config:      ${SETTINGS_FILE}            ${GREEN}║${NC}"
echo -e "${GREEN}╠══════════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║${NC}                                                          ${GREEN}║${NC}"
echo -e "${GREEN}║${NC}  Run Claude Code:                                        ${GREEN}║${NC}"
echo -e "${GREEN}║${NC}    ${BLUE}claude${NC}                                                ${GREEN}║${NC}"
echo -e "${GREEN}║${NC}                                                          ${GREEN}║${NC}"
echo -e "${GREEN}║${NC}  Or with explicit base URL:                              ${GREEN}║${NC}"
echo -e "${GREEN}║${NC}    ${BLUE}ANTHROPIC_BASE_URL=${PROXY_URL} claude${NC}        ${GREEN}║${NC}"
echo -e "${GREEN}║${NC}                                                          ${GREEN}║${NC}"
echo -e "${GREEN}║${NC}  Stop proxy:                                             ${GREEN}║${NC}"
echo -e "${GREEN}║${NC}    ${BLUE}kill ${PROXY_PID}${NC}                                             ${GREEN}║${NC}"
echo -e "${GREEN}║${NC}                                                          ${GREEN}║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════╝${NC}"
