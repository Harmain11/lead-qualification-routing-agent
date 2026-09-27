# OpenAI workspace BYOK

OpenAI lead analysis is configured per workspace. Production does not use an operator-owned
OpenAI key for customer inference.

## Credential and model lifecycle

- Users with `integrations.manage` submit a dedicated OpenAI Project API key through the
  masked integration form.
- The backend sends the key directly to the configured secret store. With the production
  encrypted database backend, AES-256-GCM protects the value and the provider connection
  stores only an opaque reference.
- API responses expose only safe state, the selected model, verification time, and safe
  validation categories. They never expose the key, ciphertext, or secret reference.
- The selected model is stored separately in provider connection metadata and must match
  `OPENAI_SUPPORTED_MODELS`.
- Replacing a key overwrites the encrypted secret. Changing a model or replacing a key moves
  the connection back to an unverified state until Test Connection succeeds.

## Verification and runtime use

Test Connection performs one minimal Responses API request with structured output and no SDK
retries. Authentication, permission, quota, model access, rate-limit, outage, timeout, and
transport failures are converted to safe status codes without provider response bodies.

For every lead, runtime resolution follows:

`lead.workspace_id → verified OpenAI connection → workspace-bound secret → allowlisted model`

No resolver path can read another workspace's secret or fall back to another customer's key.
Missing, disconnected, invalid, or unavailable OpenAI connections preserve deterministic lead
analysis and do not interrupt Gmail or CRM ingestion. A failure during a live OpenAI analysis
also falls back deterministically and marks the analysis for manual review.

## Billing and setup guidance

OpenAI API usage is billed directly to the customer's OpenAI account. Use a dedicated OpenAI
Project and project-scoped API key, restrict permissions where possible, configure project
budgets and limits, and rotate the key periodically. Do not use a personal master key.
