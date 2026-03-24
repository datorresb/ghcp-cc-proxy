---
id: TASK-2
title: Fix thinking streaming response
status: Done
assignee: []
created_date: '2026-03-24 22:06'
updated_date: '2026-03-24 22:37'
labels:
  - critical
  - thinking
milestone: M1
dependencies:
  - TASK-1
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Modify _stream_from_copilot_sse() to detect reasoning_content in SSE deltas and convert to thinking_delta events. Track state: thinking block at index 0, text block at index 1.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 reasoning_content deltas convert to thinking_delta SSE events
- [ ] #2 content_block_start with type thinking emitted at first reasoning delta
- [ ] #3 content_block_stop emitted when reasoning ends and content starts
- [ ] #4 Text content uses separate content_block at index 1
- [ ] #5 Backward compatible when no reasoning_content present
<!-- AC:END -->
