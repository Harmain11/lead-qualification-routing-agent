---
name: Gmail message presentation
description: Keep incoming Gmail inquiries readable while preserving supporting warnings and footer evidence.
---

Show the sender's authored inquiry as the primary message, with greeting, body, sign-off, and signature separated into readable paragraphs. Keep external-sender/security warnings, unsubscribe instructions, confidentiality notices, and link references in collapsed supporting details. Deduplicate repeated notices in the readable presentation while preserving the complete raw source.

**Why:** The user reported that real Gmail messages displayed sender warnings, opt-out text, duplicate confidentiality notices, and links inline as a dense block.

**How to apply:** Use the shared Gmail body splitter for display, keep the raw email available as evidence, and do not label a notice-only email as empty when its supporting details remain available.
