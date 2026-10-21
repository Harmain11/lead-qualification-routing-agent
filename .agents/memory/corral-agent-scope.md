---
name: Corral Agent scope and approvals
description: The requested scope and confirmation rule for Corral's app-wide assistant.
---

Corral Agent is intended to be available throughout the workspace and support guidance, content generation, and tasks across leads/qualification/routing/follow-ups; email/meetings/CRM; dashboard/analytics/reports/automations; and ICP/integrations/settings/team access. Every data or configuration mutation must be proposed and receive explicit user approval before execution. Chat answers and generated drafts may be returned without changing workspace state.

**Why:** the user explicitly requested an assistant that can access all areas of the platform, answer and generate content, do tasks, and ask permission before applying changes.

**How to apply:** Add capabilities through typed, permission-scoped tools backed by existing APIs. Do not grant unrestricted database or provider writes. Keep a clear review-and-approve step before every mutation.
