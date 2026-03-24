---
id: TASK-4
title: Add image content block conversion
status: Done
assignee: []
created_date: '2026-03-24 22:06'
updated_date: '2026-03-24 22:37'
labels:
  - images
milestone: M2
dependencies: []
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
In _anthropic_to_openai() content block loop, handle type image. Convert base64 source to OpenAI image_url format: data:{media_type};base64,{data}. Support mixed text+image as multimodal content array.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Base64 image blocks convert to image_url with data URI
- [ ] #2 Supports png, jpeg, gif, webp media types
- [ ] #3 Mixed text+image produces multimodal content array
- [ ] #4 URL source type passes through
<!-- AC:END -->
