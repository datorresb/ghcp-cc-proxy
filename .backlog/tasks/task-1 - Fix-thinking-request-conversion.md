---
id: TASK-1
title: Fix thinking request conversion
status: Done
assignee: []
created_date: '2026-03-24 22:06'
updated_date: '2026-03-24 22:37'
labels:
  - critical
  - thinking
milestone: M1
dependencies: []
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Modify _anthropic_to_openai() to map thinking.budget_tokens to reasoning_effort (low/medium/high). Thresholds: ≤2048→low, ≤16384→medium, >16384→high.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 thinking.budget_tokens maps to reasoning_effort in OpenAI body
- [ ] #2 No reasoning_effort when thinking absent or disabled
- [ ] #3 Unit test covers all three thresholds
<!-- AC:END -->
