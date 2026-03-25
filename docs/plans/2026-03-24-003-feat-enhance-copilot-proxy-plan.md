---
title: "feat: Enhance Copilot proxy — thinking, images, external models, retry, logging, tests, docs"
type: feat
status: completed
date: 2026-03-24
origin: .backlog/backlog.md
---

# feat: Enhance Copilot proxy — thinking, images, external models, retry, logging, tests, docs

## Overview

The proxy works for basic text requests but lacks critical features for full Claude Code compatibility: extended thinking support, image handling, external model config, retry logic, structured logging, integration tests, and proper documentation. This plan addresses all 7 milestones (M1–M7) from the backlog.

## Problem Frame

Claude Code sends requests that the proxy currently drops or mishandles:
- **Thinking**: `thinking.budget_tokens` is ignored — never sent to Copilot, and `reasoning_content` in responses is silently discarded
- **Images**: `{type: "image"}` content blocks are skipped during conversion
- **Models**: Hardcoded model map requires code changes to update
- **Reliability**: No retry on transient errors (429, 502, 503)
- **Observability**: Uses `print()` instead of `logging` module
- **Testing**: No integration tests against real Copilot API
- **Onboarding**: README and setup.sh need improvement

## Requirements Trace

- R1. Extended thinking requests pass `reasoning_effort` to Copilot based on `thinking.budget_tokens`
- R2. Streaming responses convert `reasoning_content` delta → `thinking_delta` SSE events
- R3. Non-streaming responses convert `reasoning_content` → `{type: "thinking"}` content block
- R4. Image content blocks convert to OpenAI `image_url` format
- R5. Model mappings loaded from external `models.json`, fallback to hardcoded defaults
- R6. `/v1/models` returns real Copilot models, cached
- R7. Transient errors (429, 502, 503, 504) trigger retry with exponential backoff
- R8. Token refreshes proactively 60s before expiry
- R9. All `print()` replaced with `logging` module, level configurable via `LOG_LEVEL`
- R10. Integration tests cover all major endpoints
- R11. README enables Codespace setup in <2 minutes
- R12. `setup.sh` is single-command with error detection

## Scope Boundaries

- Single file (`copilot_proxy.py`) plus `models.json`, tests, README, setup.sh
- Zero external dependencies — stdlib only
- No `cache_control` support (Copilot limitation)
- No `citations` support
- No PDT (prompt caching) support
- Backward compatible — if thinking/images absent, behaves as before

## Context & Research

### Relevant Code and Patterns

- `copilot_proxy.py` — 523 lines, all proxy logic
- `_anthropic_to_openai()` (line ~107) — request conversion, currently skips image blocks and thinking
- `_openai_to_anthropic()` (line ~163) — response conversion, currently ignores `reasoning_content`
- `_stream_from_copilot_sse()` (line ~236) — SSE streaming, currently ignores `reasoning_content` in deltas
- `_get_token()` / `_fetch_token()` — token management, no proactive refresh or retry
- Reference TS project: `temp/ClaudeCode-Copilot-Proxy/src/services/anthropic-service.ts`

### Reference Project Key Patterns

From the TypeScript reference implementation:
- **Thinking request**: Maps `thinking.budget_tokens` → `reasoning_effort` (low ≤2048, medium ≤16384, high >16384)
- **Thinking response**: Reads `message.reasoning_content` → `{type: 'thinking', thinking: reasoningContent}` content block before text
- **Image types**: Supports `base64` and `url` source types with media_type `image/jpeg|png|gif|webp`

## Key Technical Decisions

- **`reasoning_effort` over raw `thinking` passthrough**: Copilot uses OpenAI's `reasoning_effort` parameter (low/medium/high), not Anthropic's raw `thinking` object. Map budget_tokens to effort levels following the reference project's thresholds.
- **`reasoning_content` field name**: Copilot returns thinking in `reasoning_content` (not `reasoning`), confirmed by reference project.
- **Streaming thinking**: Use content_block index 0 for thinking, index 1+ for text. Emit `content_block_start` with type `thinking` at first reasoning delta, `content_block_stop` when reasoning ends and content starts.
- **models.json over DB**: Simple JSON file, loaded at startup, reloaded on SIGHUP or file change. No database.
- **`logging` module over print**: Standard library, configurable levels, timestamp format.
- **Retry in `_make_upstream_request()`**: New helper wrapping `urlopen` with retry logic, replaces raw calls.

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

```
Claude Code Request Flow:
┌─────────────────┐     ┌──────────────────────┐     ┌─────────────────┐
│  Claude Code     │────▶│  copilot_proxy.py    │────▶│  Copilot API    │
│                  │     │                      │     │                 │
│ Anthropic API    │     │ _anthropic_to_openai  │     │ OpenAI compat   │
│ + thinking       │     │   + images            │     │ + reasoning_    │
│ + images         │     │   + reasoning_effort  │     │   effort        │
│                  │     │                      │     │                 │
│                  │◀────│ _stream_from_copilot  │◀────│ SSE stream      │
│ thinking_delta   │     │   + reasoning_content │     │ + reasoning_    │
│ text_delta       │     │     → thinking_delta  │     │   content delta │
└─────────────────┘     └──────────────────────┘     └─────────────────┘
```

Thinking budget mapping:
- `budget_tokens ≤ 2048` → `reasoning_effort: "low"`
- `budget_tokens ≤ 16384` → `reasoning_effort: "medium"`
- `budget_tokens > 16384` → `reasoning_effort: "high"`

## Implementation Units

### Phase 1: Core Features (Independent, Parallelizable)

- [ ] **Unit 1: Fix thinking — request conversion (M1/T1.1)**

  **Goal:** Pass `reasoning_effort` to Copilot when Claude Code sends `thinking.budget_tokens`

  **Requirements:** R1

  **Dependencies:** None

  **Files:**
  - Modify: `copilot_proxy.py` — `_anthropic_to_openai()` function
  - Test: `tests/test_proxy.py`

  **Approach:**
  - In `_anthropic_to_openai()`, check `body.get("thinking")` 
  - If `thinking.type == "enabled"` and `thinking.budget_tokens` exists, map to `reasoning_effort`
  - Add `reasoning_effort` to the returned OpenAI body dict
  - Thresholds: ≤2048 → low, ≤16384 → medium, >16384 → high

  **Patterns to follow:**
  - Reference: `temp/ClaudeCode-Copilot-Proxy/src/services/anthropic-service.ts` lines 202-211

  **Test scenarios:**
  - Request with thinking enabled and budget 1000 → reasoning_effort "low"
  - Request with thinking enabled and budget 10000 → reasoning_effort "medium"
  - Request with thinking enabled and budget 50000 → reasoning_effort "high"
  - Request without thinking → no reasoning_effort field
  - Request with thinking disabled → no reasoning_effort field

  **Verification:**
  - `reasoning_effort` appears in OpenAI body when thinking is enabled
  - No reasoning_effort when thinking absent or disabled

- [ ] **Unit 2: Fix thinking — streaming response (M1/T1.2)**

  **Goal:** Convert `reasoning_content` in SSE deltas to Anthropic `thinking_delta` events

  **Requirements:** R2

  **Dependencies:** Unit 1

  **Files:**
  - Modify: `copilot_proxy.py` — `_stream_from_copilot_sse()` function
  - Test: `tests/test_proxy.py`

  **Approach:**
  - Track state: `in_thinking` flag, `content_block_index` counter
  - When delta contains `reasoning_content`: if first reasoning delta, emit `content_block_start` with `{type: "thinking"}` at index 0, then emit `content_block_delta` with `{type: "thinking_delta", thinking: text}`
  - When delta switches from `reasoning_content` to `content`: emit `content_block_stop` for thinking block, emit new `content_block_start` with `{type: "text"}` at index 1
  - When delta contains `content` (no prior reasoning): use index 0 as before (backward compatible)

  **Test scenarios:**
  - Stream with reasoning_content followed by content → thinking block then text block
  - Stream with only content (no reasoning) → single text block at index 0
  - Stream with empty reasoning_content → skip thinking block
  - Multiple reasoning_content chunks → multiple thinking_delta events

  **Verification:**
  - SSE output contains thinking_delta events before text_delta events
  - Content block indices are sequential and correct

- [ ] **Unit 3: Fix thinking — non-streaming response (M1/T1.3)**

  **Goal:** Convert `reasoning_content` in non-streaming responses to thinking content block

  **Requirements:** R3

  **Dependencies:** Unit 1

  **Files:**
  - Modify: `copilot_proxy.py` — `_openai_to_anthropic()` function
  - Test: `tests/test_proxy.py`

  **Approach:**
  - Read `message.reasoning_content` from OpenAI response (in addition to `message.content`)
  - Build content array: if reasoning_content present, prepend `{type: "thinking", thinking: reasoning_content}` before `{type: "text", text: content}`
  - Backward compatible: if no reasoning_content, content array is unchanged

  **Patterns to follow:**
  - Reference: `temp/ClaudeCode-Copilot-Proxy/src/services/anthropic-service.ts` lines 253-263

  **Test scenarios:**
  - Response with reasoning_content and content → [thinking, text]
  - Response with only content → [text]
  - Response with reasoning_content but empty content → [thinking]
  - Response with empty reasoning_content → [text] only

  **Verification:**
  - Thinking block appears before text block when reasoning_content present

- [ ] **Unit 4: Image support (M2/T2.1)**

  **Goal:** Convert Anthropic image content blocks to OpenAI image_url format

  **Requirements:** R4

  **Dependencies:** None

  **Files:**
  - Modify: `copilot_proxy.py` — `_anthropic_to_openai()` function, content block loop
  - Test: `tests/test_proxy.py`

  **Approach:**
  - In the content block loop that currently handles `text`, `tool_result`, `tool_use`, add handling for `image` type
  - Convert `{type: "image", source: {type: "base64", media_type: "image/png", data: "..."}}` to OpenAI multimodal format: `{type: "image_url", image_url: {url: "data:image/png;base64,..."}}`
  - Messages with mixed text+image become an array of content parts (OpenAI multimodal message format) instead of concatenated text
  - Handle `source.type == "url"` as passthrough

  **Test scenarios:**
  - Single image block → image_url with data URI
  - Text + image mixed → multimodal content array
  - Multiple images → multiple image_url entries
  - Supported media types: image/png, image/jpeg, image/gif, image/webp
  - Image with URL source type → passthrough URL

  **Verification:**
  - Image blocks produce valid OpenAI image_url format
  - Mixed content messages work correctly

- [ ] **Unit 5: External models.json (M3/T3.1 + T3.2)**

  **Goal:** Load model mappings from `models.json`, make `/v1/models` dynamic with caching

  **Requirements:** R5, R6

  **Dependencies:** None

  **Files:**
  - Already created: `models.json`
  - Modify: `copilot_proxy.py` — model loading, `_map_model()`, `/v1/models` handler
  - Test: `tests/test_proxy.py`

  **Approach:**
  - At startup, try to load `models.json` from script directory. If missing, use hardcoded defaults.
  - Replace hardcoded `MODEL_MAP` with loaded data
  - For `/v1/models`: query `{endpoint}/models` with Copilot token, cache for 5 minutes (configurable via `MODELS_CACHE_TTL` env var)
  - On cache miss or error, fall back to models.json data
  - Log model count on load

  **Test scenarios:**
  - models.json exists → loaded successfully
  - models.json missing → hardcoded defaults used
  - models.json invalid JSON → hardcoded defaults used, warning logged
  - Unknown model in request → pass-through (no mapping)
  - /v1/models → returns cached upstream models
  - /v1/models when upstream fails → returns models.json defaults

  **Verification:**
  - Startup log shows model count
  - /v1/models returns model list
  - Pass-through works for unmapped models

- [ ] **Unit 6: Retry with exponential backoff (M4/T4.1 + T4.2)**

  **Goal:** Add retry logic for transient errors and proactive token refresh

  **Requirements:** R7, R8

  **Dependencies:** None

  **Files:**
  - Modify: `copilot_proxy.py` — new `_make_upstream_request()` helper, modify `_get_token()`
  - Test: `tests/test_proxy.py`

  **Approach:**
  - New function `_make_upstream_request(url, headers, body, timeout, max_retries=3)` wrapping `urlopen`
  - On HTTPError with status in {429, 502, 503, 504}: sleep `2^attempt + random(0, 1)` and retry
  - On 401: force token refresh and retry once
  - On 400, 403, 404: raise immediately (not transient)
  - On ConnectionError/TimeoutError: retry with backoff
  - Log each retry attempt
  - Modify `_get_token()`: refresh 60 seconds before `_cache["expires"]`; if refresh fails, retry up to 3 times with backoff before raising

  **Test scenarios:**
  - 429 → retries and eventually succeeds
  - 502 → retries with backoff
  - 401 → refreshes token and retries once
  - 400 → raises immediately without retry
  - All retries exhausted → raises final error
  - Token refreshes proactively before expiry

  **Verification:**
  - Transient errors are retried up to max_retries times
  - Non-transient errors fail immediately
  - Token refresh happens before expiry

- [ ] **Unit 7: Migrate to logging module (M5/T5.1)**

  **Goal:** Replace all `print()` with `logging`, configurable via `LOG_LEVEL`

  **Requirements:** R9

  **Dependencies:** None

  **Files:**
  - Modify: `copilot_proxy.py` — add `import logging`, replace all `print()`, configure at startup
  - Test: `tests/test_proxy.py`

  **Approach:**
  - Add `import logging` and `import random` (for retry jitter) to imports
  - At module level: `logger = logging.getLogger("copilot-proxy")`
  - In `main()`: `logging.basicConfig(level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO), format="%(asctime)s [%(levelname)s] %(message)s")`
  - Replace all `print(f"[proxy] ...")` with appropriate `logger.info/debug/warning/error`
  - Server start/stop → `logger.info`
  - Token refresh → `logger.debug`
  - Upstream errors → `logger.error`
  - Request details → `logger.debug`
  - Override `log_message` to use `logger.debug`

  **Test scenarios:**
  - LOG_LEVEL=DEBUG → debug messages visible
  - LOG_LEVEL=WARNING → info messages suppressed
  - Default (no env var) → INFO level
  - No remaining `print()` calls in codebase (grep test)

  **Verification:**
  - `grep -n "print(" copilot_proxy.py` returns 0 matches
  - Timestamps appear in log output

### Phase 2: Integration Tests (Sequential)

- [ ] **Unit 8: Integration test suite (M6/T6.1 + T6.2)**

  **Goal:** End-to-end tests against real Copilot API

  **Requirements:** R10

  **Dependencies:** Units 1-7

  **Files:**
  - Create: `tests/test_integration.py`

  **Approach:**
  - Use `subprocess.Popen` to start proxy on random port
  - Fixture: start proxy, wait for health check, yield, kill process
  - Skip all tests if `gh auth token` fails (no Copilot access)
  - Use `urllib.request` for HTTP calls (no dependencies)

  **Test scenarios:**
  - `test_health_endpoint` — GET /health returns 200
  - `test_models_endpoint` — GET /v1/models returns model list
  - `test_simple_message` — POST /v1/messages with simple text
  - `test_streaming_message` — POST /v1/messages with stream=true
  - `test_thinking` — POST /v1/messages with thinking enabled, verify thinking content
  - `test_tool_use` — POST /v1/messages triggering tool use
  - `test_image_message` — POST /v1/messages with base64 image
  - `test_retry_on_error` — Verify transient error handling

  **Verification:**
  - `python3 -m pytest tests/test_integration.py -v` passes (or skips without credentials)

### Phase 3: Documentation

- [ ] **Unit 9: README and setup.sh (M7/T7.1 + T7.2)**

  **Goal:** Clear onboarding docs and robust setup script

  **Requirements:** R11, R12

  **Dependencies:** Units 1-8

  **Files:**
  - Modify: `README.md`
  - Modify: `setup.sh`

  **Approach:**
  - README: what it is (1 line), quick start (3 steps max), requirements, config (env vars + models.json), model table, limitations, troubleshooting (5+ issues)
  - setup.sh: verify gh CLI + auth + Copilot access + Python 3.8+, create models.json if missing, configure .claude/settings.json, start proxy, colored output, clear error messages

  **Test scenarios:**
  - Fresh Codespace: `./setup.sh` → proxy running in <2 min
  - Missing gh CLI → clear error message
  - No Copilot access → clear error message
  - Wrong Python version → clear error message

  **Verification:**
  - New user can follow README to working proxy
  - setup.sh handles all error cases gracefully

## Risks & Dependencies

- **Copilot reasoning_content field name**: Based on reference project analysis. If Copilot uses a different field name, Units 2-3 need adjustment. Mitigated by integration tests (Unit 8).
- **reasoning_effort thresholds**: Using reference project's thresholds (2048/16384). May need tuning based on real-world testing.
- **Streaming thinking format**: No reference implementation for streaming thinking. The approach (track state, emit content_block_start/stop) follows Anthropic's SSE spec but needs integration testing.
- **models.json schema**: Simple flat mapping. May need versioning if schema changes.

## System-Wide Impact

- **Interaction graph:** All changes in `copilot_proxy.py`. No external services affected except Copilot API.
- **Error propagation:** Retry logic wraps upstream calls. Failures still surface as Anthropic error responses.
- **State lifecycle risks:** Token cache already thread-safe. Model cache needs thread safety (use existing `_lock` or new lock).
- **API surface parity:** No breaking changes to existing Anthropic API surface.

## Execution Order

```
Phase 1 (parallel): Units 1-7 (M1-M5)
  Unit 1 (thinking request) ──┐
  Unit 2 (thinking stream)  ──┤ (2,3 depend on 1)
  Unit 3 (thinking non-stream)┤
  Unit 4 (images)            ──┤ independent
  Unit 5 (models)            ──┤ independent
  Unit 6 (retry)             ──┤ independent
  Unit 7 (logging)           ──┘ independent

Phase 2 (sequential): Unit 8 (tests)

Phase 3 (sequential): Unit 9 (docs)
```

## Sources & References

- **Origin document:** [.backlog/backlog.md](.backlog/backlog.md)
- Reference implementation: `temp/ClaudeCode-Copilot-Proxy/src/services/anthropic-service.ts`
- Existing plans: [docs/plans/2026-03-24-001-fix-finalize-copilot-proxy-plan.md](docs/plans/2026-03-24-001-fix-finalize-copilot-proxy-plan.md)
- Active PR: https://github.com/datorresb/ghcp-cc-proxy/pull/1
