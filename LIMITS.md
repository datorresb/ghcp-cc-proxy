# 📋 Known Limitations & Compatibility

## ✅ What Works

| Feature | Status | Notes |
|---------|--------|-------|
| 💬 Text messages | ✅ | Full Anthropic Messages API translation |
| 🌊 Streaming | ✅ | Real-time SSE with thinking, text, and tool_use |
| 🔧 Tool calling | ✅ | All 27 Claude Code tools (Bash, Write, Read, Edit, etc.) |
| 🧠 Extended thinking | ✅ | Maps `thinking.budget_tokens` → `reasoning_effort` |
| 🖼️ Images | ✅ | Base64 and URL images |
| 🔀 Model overrides | ✅ | Swap models on the fly via dashboard |
| 🤖 Multi-model | ✅ | Claude, GPT, Gemini via Copilot |

## ❌ What Doesn't Work

### 🔌 Server-Side Tools

Anthropic executes these on their servers. The proxy can't replicate them (yet).

| Tool | Status | Symptom | Workaround |
|------|--------|---------|------------|
| 🔍 **Web Search** | ❌ → 🔜 Planned | "Did 0 searches" | `curl` via Bash |
| 🌐 **Web Fetch** | ❌ → 🔜 Planned | Empty results | `curl` via Bash |
| 💻 **Code Execution** | ❌ | Not available | Run code locally via `Bash` tool |
| 🖥️ **Server Bash** | ➖ Not needed | N/A | Local `Bash` tool works fine |
| 📝 **Server Text Editor** | ➖ Not needed | N/A | Local `Edit`/`Write` tools work fine |
| 🔎 **Tool Search** | ❌ | Not available | Rarely used |

> 💡 Claude Code has **two versions** of Bash and Editor — local (executed by Claude Code) and server-side (executed by Anthropic). The local versions work through the proxy. Server-side versions aren't needed.

### 🏗️ Anthropic API Features

| Feature | Status | Impact | Workaround |
|---------|--------|--------|------------|
| 💾 **Prompt Caching** | ❌ Can't map | More tokens per request | None — Copilot limitation |
| 📁 **Files API** | ❌ Can't map | No file hosting | Use local `Read`/`Write` tools |
| 🗜️ **Context Management** | ❌ Can't map | No auto-compact for long conversations | Start new sessions |
| 📦 **Compact** | ❌ Can't map | No conversation compaction | Same as above |
| 😴 **AFK Mode** | ❌ Can't map | No background processing | Keep session active |
| 📖 **Citations** | ❌ Can't map | No source citations | N/A |
| 📄 **PDF/Document blocks** | ❌ Can't map | No native PDF analysis | `pdftotext` via Bash |
| 🔒 **Redact Thinking** | ➖ Not needed | Thinking passes through as-is | Not a problem |

### ⚠️ Partially Working

| Feature | Status | Notes |
|---------|--------|-------|
| 🧠 **Extended Thinking** | ⚠️ Simplified | `budget_tokens` → `reasoning_effort` (low/medium/high). No interleaved thinking |
| 🔢 **Token Counting** | ⚠️ Approximate | Character-based (÷4), not exact tokenization |
| 📐 **Structured Outputs** | ⚠️ Model-dependent | Works if Copilot model supports it |

## 🔜 Roadmap: Proxy-Side Solutions

These server-side features could be implemented **in the proxy itself**:

| Feature | Feasibility | Approach |
|---------|-------------|----------|
| 🔍 **Web Search** | ✅ High | Proxy intercepts `web_search` tool, executes search via DuckDuckGo/Google scraping, returns results |
| 🌐 **Web Fetch** | ✅ High | Proxy intercepts `web_fetch` tool, fetches URL with `urllib`, returns content |
| 💻 **Code Execution** | ⚠️ Risky | Proxy could run Python in subprocess — but security concern |
| 📄 **PDF Analysis** | ⚠️ Medium | Proxy could extract text from PDF URLs before forwarding |

## 🤖 Model-Specific Notes

### 🟣 Claude Models via Copilot
- Tool calling works naturally (same model family)
- System prompt designed for Claude — works as expected
- Thinking/reasoning maps to `reasoning_effort`

### 🟢 GPT Models via Override
- Tool calling works (native OpenAI function calling)
- GPT may interpret Claude Code's system prompt differently
- Some Claude-specific instructions may be ignored
- JSON schema for tools is universal — arguments work fine

### 🔵 Gemini Models via Override
- Tool calling works (supports function calling)
- May behave differently with Claude Code's system prompt
- Less tested than Claude/GPT paths

## ⏱️ Rate Limits

Copilot has its own rate limits separate from Anthropic's:
- 🔄 Proxy retries automatically on 429 with exponential backoff
- ⚡ Heavy tool-use sessions may hit limits faster
- 📊 Each tool call = 1 full API request (request → tool_use → tool_result → request)

## 🔢 Token Counting

The `/v1/messages/count_tokens` endpoint returns **approximate** counts (characters ÷ 4). Not exact tokenization but sufficient for Claude Code's needs.
