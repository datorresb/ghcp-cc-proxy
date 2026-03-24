---
title: "feat: Move Claude Code configuration into project repository"
type: feat
status: completed
date: 2026-03-24
---

# feat: Move Claude Code configuration into project repository

## Overview

Claude Code is currently configured via global `~/.claude/settings.json` (written by `setup.sh`), which is invisible to anyone browsing the repo. Move this configuration into project-level files so it's versioned, visible, and portable. Also update `setup.sh` so it no longer silently writes global config.

## Problem Frame

The `setup.sh` script writes Claude Code environment variables (`ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `DISABLE_NON_ESSENTIAL_MODEL_CALLS`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`) into `~/.claude/settings.json`. This means:
- Configuration is invisible when browsing the repo
- Contributors have to run `setup.sh` or manually configure Claude Code
- The global settings may conflict with other projects' Claude Code usage

Claude Code supports project-level settings via `.claude/settings.json` in the repository root. Moving configuration there makes it self-documenting and portable.

## Requirements Trace

- R1. Project-level `.claude/settings.json` contains proxy env vars so Claude Code auto-configures when opened in this repo
- R2. `setup.sh` updated to prefer project-level config and only write global config as a fallback or supplemental step
- R3. `CLAUDE.md` provides project context/instructions for Claude Code sessions in this repo

## Scope Boundaries

- Not changing how the proxy itself works
- Not modifying `copilot_proxy.py`
- Not removing the ability to configure globally — just making project-level the primary path

## Context & Research

### Relevant Code and Patterns

- `setup.sh` lines 105-133: writes `~/.claude/settings.json` with env block
- `~/.claude/settings.json`: current global config with env vars + model preference
- Claude Code reads `.claude/settings.json` in the project root for project-scoped settings

### Key Technical Decisions

- **Project `.claude/settings.json` for env vars**: The proxy token `sk-copilot-proxy` is a dummy placeholder (proxy authenticates via `gh auth token`), so it's safe to commit
- **`CLAUDE.md` for project instructions**: Standard way Claude Code discovers project context
- **Keep `setup.sh` global config as fallback**: Users running `setup.sh` outside the project directory still need it, but add a comment explaining the project-level config is preferred

## Implementation Units

- [ ] **Unit 1: Create `.claude/settings.json`**

**Goal:** Project-level Claude Code settings with proxy env vars

**Requirements:** R1

**Dependencies:** None

**Files:**
- Create: `.claude/settings.json`

**Approach:**
- Include `env` block with `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `DISABLE_NON_ESSENTIAL_MODEL_CALLS`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`
- Use `http://localhost:8080` as default (matches `PORT` default in proxy)

**Verification:**
- File exists and is valid JSON
- Claude Code reads these env vars when opening a session in the repo

- [ ] **Unit 2: Create `CLAUDE.md`**

**Goal:** Provide project context and instructions for Claude Code sessions

**Requirements:** R3

**Dependencies:** None

**Files:**
- Create: `CLAUDE.md`

**Approach:**
- Brief project description (what the proxy does)
- Key commands (start proxy, run tests)
- Architecture notes (zero-dependency Python, stdlib only)
- Reference that `.claude/settings.json` handles env configuration

**Verification:**
- Claude Code loads project instructions when opening a session

- [ ] **Unit 3: Update `setup.sh` to reference project config**

**Goal:** Make `setup.sh` aware of project-level config; reduce reliance on global settings

**Requirements:** R2

**Dependencies:** Unit 1

**Files:**
- Modify: `setup.sh`

**Approach:**
- In section 5 ("Configure Claude Code"), add a check: if running from within the repo and `.claude/settings.json` exists, inform the user that project-level config is already in place
- Still write global config as fallback for users running setup from a different directory
- Add a comment explaining the two config levels

**Verification:**
- `setup.sh` runs without errors
- When run from the project directory, it informs user that project config exists

## Risks & Dependencies

- Users who already have global `~/.claude/settings.json` will have both global and project-level config. Claude Code merges these with project-level taking precedence, which is the desired behavior.
