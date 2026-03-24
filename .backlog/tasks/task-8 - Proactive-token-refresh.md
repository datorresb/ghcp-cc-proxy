---
id: TASK-8
title: Proactive token refresh
status: Done
assignee: []
created_date: '2026-03-24 22:07'
updated_date: '2026-03-24 22:37'
labels:
  - retry
milestone: M4
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Modify _get_token() to refresh 60s before expiry. If refresh fails, retry up to 3 times with backoff. Keep thread-safe with existing _lock.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Token refreshes 60s before expiry
- [ ] #2 Retry with backoff on refresh failure (3 attempts)
- [ ] #3 Thread-safe using existing _lock
<!-- AC:END -->
