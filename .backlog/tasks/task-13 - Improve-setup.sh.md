---
id: TASK-13
title: Improve setup.sh
status: Done
assignee: []
created_date: '2026-03-24 22:08'
updated_date: '2026-03-24 22:37'
labels:
  - docs
milestone: M7
dependencies: []
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Single-command setup: verify gh CLI + auth + Copilot access + Python 3.8+, create models.json if missing, configure .claude/settings.json, start proxy. Colored output, clear errors.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 One command: ./setup.sh
- [ ] #2 Detects and reports errors for missing gh CLI, no auth, no Copilot, wrong Python
- [ ] #3 Creates models.json and .claude/settings.json if missing
- [ ] #4 Works in Codespace and local Linux/macOS
<!-- AC:END -->
