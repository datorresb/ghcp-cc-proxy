---
id: TASK-10
title: Integration test framework setup
status: Done
assignee: []
created_date: '2026-03-24 22:07'
updated_date: '2026-03-24 22:37'
labels:
  - tests
milestone: M6
dependencies:
  - TASK-1
  - TASK-4
  - TASK-5
  - TASK-7
  - TASK-9
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Create tests/test_integration.py with subprocess to start proxy on random port. Pytest fixture for start/stop. Auto-skip if gh auth token fails.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Proxy starts and stops automatically via fixture
- [ ] #2 Tests skip without Copilot credentials
- [ ] #3 Random port avoids conflicts
<!-- AC:END -->
