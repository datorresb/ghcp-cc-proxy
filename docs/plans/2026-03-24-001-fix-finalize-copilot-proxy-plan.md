---
title: "fix: Finalize lightweight Copilot proxy — threading fix, config cleanup, and repo hygiene"
type: fix
status: active
date: 2026-03-24
---

# fix: Finalize lightweight Copilot proxy — threading fix, config cleanup, and repo hygiene

## Overview

The core proxy (`copilot_proxy.py`) and setup script (`setup.sh`) are built and tested. This plan addresses the remaining issues discovered during testing: the server hangs under concurrent requests (single-threaded HTTPServer), the setup script needs the dummy auth token config already added, and uncommitted changes need to be pushed.

## Problem Frame

During live testing, the proxy hung after a streaming request because Python's `HTTPServer` is single-threaded — one long-running SSE connection blocks all subsequent requests. Claude Code makes concurrent requests, so this is a hard blocker. The threading fix is already applied locally but not committed.

## Requirements Trace

- R1. Proxy must handle concurrent requests without hanging
- R2. Setup script must configure Claude Code with all required env vars (`ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `DISABLE_NON_ESSENTIAL_MODEL_CALLS`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`)
- R3. All changes committed and pushed to `origin/main`
- R4. `temp/` directory excluded from git via `.gitignore`
- R5. Old `INSTALLATION_GUIDE.md` removed (superseded by README)

## Scope Boundaries

- No new features — this is cleanup of existing work
- No changes to API translation logic (already tested and working)
- No changes to model mapping

## Key Technical Decisions

- **ThreadingMixIn over asyncio**: Minimal change, stdlib-only, preserves the "zero dependencies" goal. `ThreadingMixIn` with `daemon_threads=True` handles concurrent requests correctly for this use case.

## Implementation Units

- [ ] **Unit 1: Commit threading fix + config changes**

  **Goal:** Push the already-applied `ThreadingHTTPServer` fix and `setup.sh` config improvements to remote.

  **Requirements:** R1, R2, R3, R4, R5

  **Dependencies:** None — changes are already applied locally

  **Files:**
  - Modified: `copilot_proxy.py` (ThreadingMixIn already applied)
  - Modified: `setup.sh` (env vars config already applied)
  - Added: `.gitignore` (already committed)
  - Deleted: `INSTALLATION_GUIDE.md` (already committed)

  **Approach:**
  - Stage `copilot_proxy.py` and `setup.sh`
  - Commit with descriptive message
  - Push to `origin/main`

  **Verification:**
  - `git status` shows clean working tree
  - `git log` shows new commit on `origin/main`

- [ ] **Unit 2: Verify proxy works after restart**

  **Goal:** Confirm the threaded proxy handles concurrent requests correctly

  **Requirements:** R1

  **Dependencies:** Unit 1

  **Files:**
  - No changes — verification only

  **Test scenarios:**
  - Health check returns OK
  - Non-streaming `/v1/messages` returns valid Anthropic response
  - Streaming `/v1/messages` returns valid SSE events
  - After streaming request, health check still responds (no hang)
  - 404 for unknown endpoints

  **Verification:**
  - All endpoints respond correctly
  - Server remains responsive after streaming requests

## Risks & Dependencies

- **Low risk:** All code changes are already applied and individually tested. This plan is formalizing and committing.
