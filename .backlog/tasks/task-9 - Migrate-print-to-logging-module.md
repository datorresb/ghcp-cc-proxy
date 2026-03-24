---
id: TASK-9
title: Migrate print to logging module
status: Done
assignee: []
created_date: '2026-03-24 22:07'
updated_date: '2026-03-24 22:37'
labels:
  - logging
milestone: M5
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Replace all print() with Python logging module. Add import logging, configure via LOG_LEVEL env var (default INFO). Format: timestamp [LEVEL] message.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 All print() replaced with logger.info/debug/warning/error
- [ ] #2 LOG_LEVEL env var controls level (default INFO)
- [ ] #3 Format includes timestamp
- [ ] #4 Server start/stop uses INFO, token refresh uses DEBUG, errors use ERROR
- [ ] #5 No remaining print() calls in proxy code
<!-- AC:END -->
