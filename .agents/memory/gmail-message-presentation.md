---
name: Gmail message presentation
description: Keep incoming Gmail inquiries readable while preserving supporting warnings and footer evidence.
---

Show the sender's authored inquiry as the primary message, with greeting, body, sign-off, and signature separated into readable paragraphs. Keep external-sender/security warnings, unsubscribe instructions, confidentiality notices, and link references in collapsed supporting details. Deduplicate repeated notices in the readable presentation while preserving the complete raw source. Gmail may flatten the warning and inquiry into `Subject`, or append the greeting and body to a valid `Date`; recover the body instead of displaying it as metadata.

**Why:** Real Gmail messages can contain sender warnings, opt-out text, duplicate confidentiality notices, and links inline as a dense block. Flattened `Subject` content can produce an oversized subject and empty body; body text appended to `Date` can produce an oversized received timestamp and empty body.

**How to apply:** Split recognizable sender/security markers from a flattened `Subject` before formatting. For `Date`, recover text only when the prefix parses as a valid date and the suffix starts with a recognized greeting; otherwise preserve the original value. Use the shared Gmail body splitter for display, keep the raw email available as evidence, and do not label a notice-only email as empty when supporting details remain available.
