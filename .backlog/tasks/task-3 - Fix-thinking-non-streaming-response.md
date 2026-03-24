---
id: TASK-3
title: Fix thinking non-streaming response
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
Modify _openai_to_anthropic() to read message.reasoning_content and prepend {type: thinking} block before {type: text} in content array.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 reasoning_content converts to thinking block in content array
- [ ] #2 Thinking block appears BEFORE text block
- [ ] #3 Backward compatible when no reasoning_content
<!-- AC:END -->
