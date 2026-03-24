# ghcp-cc-proxy

Use **Claude Code** with your **GitHub Copilot** subscription. A single Python proxy (~300 lines, zero dependencies) translates Anthropic's Messages API into Copilot's chat completions endpoint.

```
┌─────────────┐     ┌───────────────────┐     ┌──────────────────┐
│ Claude Code  │────▶│  copilot_proxy.py │────▶│ GitHub Copilot   │
│ (Anthropic)  │◀────│  localhost:8080   │◀────│ (Claude models)  │
└─────────────┘     └───────────────────┘     └──────────────────┘
```

## Prerequisites

- **Python 3.8+**
- **GitHub CLI** (`gh`) — [install](https://cli.github.com)
- **GitHub Copilot** subscription (Individual, Business, or Enterprise)
- **Node.js 18+** (for Claude Code install)

## Quick Start

```bash
# One-liner setup (installs Claude Code, starts proxy, configures everything)
./setup.sh

# Then just run:
claude
```

## Manual Setup

```bash
# 1. Authenticate with GitHub
gh auth login -h github.com -p https -w

# 2. Install Claude Code
npm install -g @anthropic-ai/claude-code

# 3. Start the proxy
python3 copilot_proxy.py &

# 4. Run Claude Code with the proxy
ANTHROPIC_BASE_URL=http://localhost:8080 ANTHROPIC_AUTH_TOKEN=sk-copilot-proxy claude
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `8080` | Proxy listen port |
| `ANTHROPIC_BASE_URL` | — | Set to `http://localhost:8080` for Claude Code |
| `ANTHROPIC_AUTH_TOKEN` | — | Set to any non-empty string (e.g. `sk-copilot-proxy`) |
| `DISABLE_NON_ESSENTIAL_MODEL_CALLS` | — | Set to `1` to avoid extra Anthropic API calls |

## Supported Models

The proxy maps Anthropic model names to Copilot equivalents:

| Claude Code sends | Copilot receives |
|-------------------|------------------|
| `claude-opus-4-5-*` | `claude-opus-4.5` |
| `claude-sonnet-4-*` | `claude-sonnet-4` |
| `claude-sonnet-4-5-*` | `claude-sonnet-4.5` |
| `claude-haiku-4-5-*` | `claude-haiku-4.5` |

Unknown models (GPT, Gemini, etc.) are passed through as-is.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/messages` | Anthropic Messages API (for Claude Code) |
| `POST` | `/v1/chat/completions` | OpenAI-compatible (for Cursor, etc.) |
| `GET` | `/v1/models` | List available models |
| `GET` | `/health` | Health check |

## How It Works

1. Claude Code sends Anthropic Messages API requests to the proxy
2. Proxy translates to OpenAI chat/completions format
3. Proxy authenticates with Copilot using `gh auth token`
4. Copilot processes the request using its Claude models
5. Proxy translates the response back to Anthropic format
6. Streaming is supported end-to-end (SSE)

## Use in Any Project

Copy `copilot_proxy.py` and `setup.sh` into any repo, or run remotely:

```bash
curl -fsSL https://raw.githubusercontent.com/datorresb/ghcp-cc-proxy/main/setup.sh | bash
```

## Troubleshooting

**"Cannot get Copilot token"** — Run `gh auth login` and ensure your GitHub account has Copilot access.

**Proxy won't start** — Check `/tmp/copilot-proxy.log` for errors.

**Claude Code ignores proxy** — Verify `~/.claude/settings.json` contains `"apiBaseUrl": "http://localhost:8080"` or set `ANTHROPIC_BASE_URL` env var.