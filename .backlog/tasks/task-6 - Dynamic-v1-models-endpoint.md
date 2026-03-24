---
id: TASK-6
title: Dynamic /v1/models endpoint
status: Done
assignee: []
created_date: '2026-03-24 22:07'
updated_date: '2026-03-24 22:37'
labels:
  - models
milestone: M3
dependencies:
  - TASK-5
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
GET /v1/models queries Copilot API for real models, caches for 5min (configurable via MODELS_CACHE_TTL). Falls back to models.json defaults on error.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 /v1/models returns real Copilot models
- [ ] #2 Cache with configurable TTL (default 5min)
- [ ] #3 Fallback to models.json defaults on upstream error
<!-- AC:END -->
