---
id: TASK-5
title: Load models from external models.json
status: Done
assignee: []
created_date: '2026-03-24 22:06'
updated_date: '2026-03-24 22:37'
labels:
  - models
milestone: M3
dependencies: []
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
At startup, load models.json from script directory. Replace hardcoded MODEL_MAP. Fallback to hardcoded defaults if file missing or invalid. Log model count.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 models.json loaded at startup
- [ ] #2 Fallback to hardcoded defaults if file missing
- [ ] #3 Fallback to defaults if JSON invalid (with warning log)
- [ ] #4 Unknown models pass through without mapping
- [ ] #5 Startup log shows loaded model count
<!-- AC:END -->
