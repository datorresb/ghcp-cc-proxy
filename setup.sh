#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────────────
# setup.sh — Set up and start the GitHub Copilot → Claude Code proxy
# ─────────────────────────────────────────────────────────────────────

PORT="${PORT:-8080}"
HOST="${HOST:-127.0.0.1}"
PROXY_URL="http://${HOST}:${PORT}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROXY_SCRIPT="${SCRIPT_DIR}/copilot_proxy.py"

# ── Colors ───────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

step()  { echo -e "${BLUE}[·]${NC} $*"; }
ok()    { echo -e "${GREEN}[✓]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
fail()  { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }

# ── Step 1: Check gh CLI ────────────────────────────────────────────

step "Checking for gh CLI..."
if ! command -v gh >/dev/null 2>&1; then
    fail "gh CLI not found. Install it: https://cli.github.com"
fi
ok "gh CLI found: $(gh --version | head -1)"

# ── Step 2: Check gh auth ───────────────────────────────────────────

step "Checking GitHub authentication..."
if ! gh auth token -h github.com >/dev/null 2>&1; then
    fail "gh CLI not authenticated. Run: gh auth login -h github.com -p https -w"
fi
ok "GitHub authentication verified"

# ── Step 3: Check Copilot access ────────────────────────────────────

step "Checking GitHub Copilot access..."
if ! python3 -c "
import subprocess, json
from urllib.request import Request, urlopen
t = subprocess.check_output(['gh','auth','token','-h','github.com'], text=True).strip()
r = Request('https://api.github.com/copilot_internal/v2/token',
    headers={'Authorization': f'token {t}', 'Editor-Version': 'vscode/1.96.0',
             'Editor-Plugin-Version': 'copilot-chat/0.40.0'})
urlopen(r, timeout=10)
" 2>/dev/null; then
    fail "Cannot get Copilot token. Ensure your GitHub account has an active Copilot subscription."
fi
ok "GitHub Copilot access verified"

# ── Step 4: Check Python 3.8+ ───────────────────────────────────────

step "Checking Python version..."
if ! command -v python3 >/dev/null 2>&1; then
    fail "python3 not found. Install Python 3.8+."
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PYTHON_MAJOR=$(python3 -c 'import sys; print(sys.version_info.major)')
PYTHON_MINOR=$(python3 -c 'import sys; print(sys.version_info.minor)')

if [ "$PYTHON_MAJOR" -lt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 8 ]; }; then
    fail "Python ${PYTHON_VERSION} found, but 3.8+ is required."
fi
ok "Python ${PYTHON_VERSION}"

# ── Step 5: Create models.json if missing ────────────────────────────

step "Checking models.json..."
MODELS_FILE="${SCRIPT_DIR}/models.json"
if [ ! -f "$MODELS_FILE" ]; then
    cat > "$MODELS_FILE" <<'MODELS'
{
  "model_map": {
    "claude-opus-4-6": "claude-opus-4.6",
    "claude-opus-4-6-1m": "claude-opus-4.6-1m",
    "claude-opus-4-6[1m]": "claude-opus-4.6-1m",
    "claude-sonnet-4-6": "claude-sonnet-4.6",
    "claude-haiku-4-5": "claude-haiku-4.5",
    "claude-sonnet-4-20250514": "claude-sonnet-4",
    "claude-3-5-sonnet-20241022": "claude-3.5-sonnet",
    "opus": "claude-opus-4.6",
    "opus[1m]": "claude-opus-4.6-1m",
    "sonnet": "claude-sonnet-4.6",
    "haiku": "claude-haiku-4.5"
  },
  "default_model": "claude-sonnet-4.6"
}
MODELS
    ok "Created models.json with default mappings"
else
    ok "models.json exists"
fi

# ── Step 6: Create .claude/settings.json if missing ──────────────────

step "Checking .claude/settings.json..."
CLAUDE_CONFIG="${SCRIPT_DIR}/.claude/settings.json"
if [ ! -f "$CLAUDE_CONFIG" ]; then
    mkdir -p "${SCRIPT_DIR}/.claude"
    cat > "$CLAUDE_CONFIG" <<SETTINGS
{
  "env": {
    "ANTHROPIC_BASE_URL": "${PROXY_URL}",
    "ANTHROPIC_AUTH_TOKEN": "sk-copilot-proxy",
    "DISABLE_NON_ESSENTIAL_MODEL_CALLS": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"
  }
}
SETTINGS
    ok "Created .claude/settings.json"
else
    ok ".claude/settings.json exists"
fi

# ── Step 7: Start the proxy ─────────────────────────────────────────

if [ ! -f "$PROXY_SCRIPT" ]; then
    fail "copilot_proxy.py not found at ${PROXY_SCRIPT}"
fi

# Kill any existing proxy on this port
if lsof -ti:"${PORT}" >/dev/null 2>&1; then
    warn "Port ${PORT} in use — stopping existing process"
    kill "$(lsof -ti:"${PORT}")" 2>/dev/null || true
    sleep 1
fi

step "Starting proxy on ${HOST}:${PORT}..."
PORT="${PORT}" HOST="${HOST}" python3 "$PROXY_SCRIPT" &
PROXY_PID=$!
disown "$PROXY_PID" 2>/dev/null || true

# Wait for proxy to be ready
for i in $(seq 1 15); do
    if curl -sf "http://localhost:${PORT}/health" >/dev/null 2>&1; then
        ok "Proxy running (PID ${PROXY_PID})"
        break
    fi
    if ! kill -0 "$PROXY_PID" 2>/dev/null; then
        fail "Proxy process exited. Check output above for errors."
    fi
    if [ "$i" -eq 15 ]; then
        fail "Proxy failed to start within 15s. Check output above for errors."
    fi
    sleep 1
done

# ── Done ─────────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}Setup complete.${NC}"
echo ""
echo -e "  Proxy:   http://${HOST}:${PORT}"
echo -e "  PID:     ${PROXY_PID}"
echo -e "  Health:  curl http://localhost:${PORT}/health"
echo ""
echo -e "  Run Claude Code:"
echo -e "    ${BLUE}claude${NC}"
echo ""
echo -e "  Stop proxy:"
echo -e "    ${BLUE}kill ${PROXY_PID}${NC}"
