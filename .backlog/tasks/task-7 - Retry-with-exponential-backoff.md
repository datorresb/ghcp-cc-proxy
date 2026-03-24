---
id: TASK-7
title: Retry with exponential backoff
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
New _make_upstream_request() wrapping urlopen. Retry on 429/502/503/504 with sleep(2^attempt + jitter). On 401: force token refresh, retry once. No retry on 400/403/404.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Retries on 429, 502, 503, 504 with exponential backoff
- [ ] #2 No retry on 400, 401 (after refresh), 403, 404
- [ ] #3 Auto-refresh token on 401 and retry once
- [ ] #4 Log each retry attempt
- [ ] #5 Max 3 retries configurable
<!-- AC:END -->
