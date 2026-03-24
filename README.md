# GitHub Copilot → Claude Code Proxy

Lightweight Python proxy (zero dependencies) that lets Claude Code use GitHub Copilot's Anthropic models.

```
Claude Code ──▶ copilot_proxy.py ──▶ GitHub Copilot
(Anthropic API)   localhost:8080      (Claude models)
```

## Quick Start

```bash
# 1. Clone (or open in Codespace)
git clone https://github.com/datorresb/ghcp-cc-proxy.git && cd ghcp-cc-proxy

# 2. Run setup
./setup.sh

# 3. Start Claude Code — it auto-configures via .claude/settings.json
claude
```

## Requirements

- **GitHub CLI** (`gh`) authenticated with Copilot access — [install](https://cli.github.com)
- **Python 3.8+**

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `8080` | Proxy listen port |
| `HOST` | `127.0.0.1` | Bind address |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `MODELS_CACHE_TTL` | `300` | Seconds to cache `/v1/models` response |

Custom model mappings can be defined in `models.json`. The proxy loads this file at startup and falls back to built-in defaults if it's missing or invalid.

## Available Models

| Alias | Maps To |
|-------|---------|
| `claude-opus-4-6` | `claude-opus-4.6` |
| `claude-opus-4-6-1m` | `claude-opus-4.6-1m` |
| `claude-opus-4-6[1m]` | `claude-opus-4.6-1m` |
| `claude-sonnet-4-6` | `claude-sonnet-4.6` |
| `claude-haiku-4-5` | `claude-haiku-4.5` |
| `claude-sonnet-4-20250514` | `claude-sonnet-4` |
| `claude-3-5-sonnet-20241022` | `claude-3.5-sonnet` |
| `opus` | `claude-opus-4.6` |
| `opus[1m]` | `claude-opus-4.6-1m` |
| `sonnet` | `claude-sonnet-4.6` |
| `haiku` | `claude-haiku-4.5` |

Unknown models are passed through as-is.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/messages` | Anthropic Messages API |
| `POST` | `/v1/chat/completions` | OpenAI-compatible pass-through |
| `POST` | `/v1/messages/count_tokens` | Token counting |
| `GET` | `/v1/models` | List available models |
| `GET` | `/health` | Health check |

## Features

- **Extended thinking** — maps Anthropic `thinking` parameter to OpenAI `reasoning_effort`
- **Image support** — base64 and URL image blocks in messages
- **Streaming** — real-time SSE translation (OpenAI stream → Anthropic stream)
- **Retry with backoff** — automatic retries on 429, 502, 503, 504 with exponential backoff
- **Structured logging** — configurable log levels via `LOG_LEVEL`
- **Token auto-management** — fetches and caches Copilot tokens, refreshes on expiry

## Limitations

- No `cache_control` support (Copilot doesn't expose prompt caching)
- No citations or PDF/document type (`pdft`) support
- Copilot rate limits apply — the proxy retries but can't bypass them
- Token counts are approximate (character-based estimation for `count_tokens`)

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `gh auth token` fails | Run `gh auth login -h github.com -p https -w` |
| Connection refused on 8080 | Check `PORT`, ensure the proxy is running |
| 401 Unauthorized | Token expired — proxy auto-refreshes, but verify with `gh auth token` |
| Model not found | Check `models.json` mapping, or use the Copilot model name directly |
| Streaming hangs | Ensure nothing is buffering between client and proxy |
| Rate limited (429) | Proxy retries automatically with backoff; reduce request frequency |
| Request body too large | Body exceeds 10 MB limit; reduce payload size |

## Architecture

Single file (`copilot_proxy.py`), Python stdlib only. Uses `ThreadingMixIn` for concurrent requests. Tokens are fetched via `gh auth token` and cached with automatic refresh.

## Testing

```bash
python3 -m pytest tests/ -v
```