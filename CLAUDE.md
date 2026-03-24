# GitHub Copilot → Claude Code Proxy

This project is a lightweight Python proxy (zero external dependencies) that bridges Claude Code to GitHub Copilot's Anthropic models. It translates Anthropic Messages API requests into OpenAI chat/completions format that GitHub Copilot understands.

## Quick Start

```bash
# Start the proxy (requires gh CLI authenticated with Copilot access)
PORT=8080 python3 copilot_proxy.py

# Run tests
python3 -m pytest tests/ -v
```

## Architecture

- **Single file**: `copilot_proxy.py` (~500 lines, stdlib only)
- **No dependencies**: Uses only Python standard library (`http.server`, `urllib`, `json`, `threading`)
- **Threading**: Uses `ThreadingMixIn` for concurrent request handling
- **Token management**: Auto-fetches and caches GitHub Copilot tokens via `gh auth token`

## Configuration

Project-level Claude Code settings are in `.claude/settings.json`. These configure:
- `ANTHROPIC_BASE_URL` → points Claude Code at the local proxy
- `ANTHROPIC_AUTH_TOKEN` → dummy token (real auth is via `gh` CLI)
- `DISABLE_NON_ESSENTIAL_MODEL_CALLS` / `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` → reduces unnecessary API calls

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/messages` | Anthropic Messages API (main endpoint) |
| POST | `/v1/chat/completions` | OpenAI-compatible pass-through |
| GET | `/v1/models` | List available models |
| GET | `/health` | Health check |

## Key Files

- `copilot_proxy.py` — Main proxy server
- `setup.sh` — One-command setup script
- `tests/test_proxy.py` — Test suite
- `.claude/settings.json` — Claude Code project configuration
