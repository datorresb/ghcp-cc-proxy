# GitHub Copilot → Claude Code Proxy

Lightweight Python proxy (zero dependencies) that lets [Claude Code](https://docs.anthropic.com/en/docs/claude-code) use GitHub Copilot's AI models.

```
Claude Code ──▶ copilot_proxy.py ──▶ GitHub Copilot API
(Anthropic API)   localhost:4141      (Claude, GPT, Gemini)
```

## Quick Start

```bash
# 1. Clone
git clone https://github.com/datorresb/ghcp-cc-proxy.git && cd ghcp-cc-proxy

# 2. Start the proxy
python3 copilot_proxy.py

# 3. In another terminal, start Claude Code — it auto-configures via .claude/settings.json
claude
```

Dashboard at `http://localhost:4141/`

## Requirements

- **GitHub CLI** (`gh`) authenticated with Copilot access — [install](https://cli.github.com)
- **Python 3.8+**

## How It Works

Claude Code sends Anthropic Messages API requests to the proxy. The proxy translates them to OpenAI chat/completions format, forwards to GitHub Copilot, and translates responses back. Claude Code never knows the difference.

Model mappings are defined in `models.json`:

```json
{
  "model_map": {
    "claude-opus-4-6": "claude-opus-4.6",
    "claude-haiku-4-5": "claude-haiku-4.5",
    "claude-sonnet-4-6": "claude-sonnet-4.6"
  },
  "available_targets": [
    "claude-opus-4.6", "claude-opus-4.6-1m", "claude-sonnet-4.6",
    "claude-haiku-4.5", "gpt-5.4", "gpt-5.4-mini", "gemini-3.1-pro-preview"
  ],
  "overrides": {}
}
```

- `model_map` — base mappings from Claude Code model names to Copilot model names
- `available_targets` — models shown in the dashboard dropdown
- `overrides` — runtime model overrides set via the dashboard (persisted automatically)

Unknown models are passed through as-is.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `4141` | Proxy listen port |
| `HOST` | `127.0.0.1` | Bind address |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `MAX_CONCURRENT` | `32` | Max simultaneous requests |
| `MODELS_CACHE_TTL` | `300` | Seconds to cache `/v1/models` response |

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Web dashboard |
| `POST` | `/v1/messages` | Anthropic Messages API |
| `POST` | `/v1/messages/count_tokens` | Token counting (approximate) |
| `POST` | `/v1/chat/completions` | OpenAI-compatible pass-through |
| `GET` | `/v1/models` | List available Copilot models |
| `GET` | `/api/config` | Get current model config |
| `POST` | `/api/config` | Set model overrides |
| `GET` | `/api/stats` | Usage statistics |
| `GET` | `/api/auth-status` | Check `gh` CLI auth status |
| `GET` | `/health` | Health check |

## Features

- **Tool support** — full Anthropic tool_use ↔ OpenAI function calling translation. Claude Code CLI tools (Bash, Write, Read, Edit, etc.) work through the proxy
- **Web dashboard** — model switching, usage monitoring, auth status at `http://localhost:4141/`
- **Model overrides** — swap models on the fly without restarting (e.g., opus → gpt-5.4)
- **Usage tracking** — live request count, token usage, per-model breakdown
- **Extended thinking** — maps `thinking.budget_tokens` to `reasoning_effort`
- **Image support** — base64 and URL images
- **Streaming** — real-time SSE translation (OpenAI → Anthropic format), including streaming tool calls
- **Retry with backoff** — 429, 502, 503, 504 with exponential backoff
- **Token auto-refresh** — fetches and caches Copilot tokens via `gh` CLI

## Testing

```bash
python3 -m pytest tests/ -v
```

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `gh auth token` fails | `gh auth login -h github.com -p https -w` |
| Connection refused | Check `PORT`, ensure proxy is running |
| 401 errors | Token expired — proxy auto-refreshes, verify with `gh auth token` |
| Model not found | Check `models.json` or use the Copilot model name directly |

## License

MIT