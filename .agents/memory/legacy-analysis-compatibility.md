---
name: Legacy analysis compatibility
description: Safely reading persisted AI analyses after strict schema changes.
---

When adding required fields to strict AI-analysis JSON, keep fresh provider-output validation strict, but use a compatibility adapter when reading older persisted records. Fill only fields that were absent in legacy records with safe, non-inferred values; never rewrite stored records as a side effect of reading them. Keep re-analysis freshness checks strict so stale or incomplete analyses are not treated as current.

**Why:** Requiring AI-owned intent scores made older saved analyses fail response validation, causing the lead-list endpoint to return an internal server error.

**How to apply:** Route every persisted-analysis read through the compatibility adapter, but validate new provider responses and cache-freshness checks against the current schema without that adapter.
