---
name: Gmail message presentation
description: Keep incoming Gmail inquiries readable while preserving supporting warnings and footer evidence.
---

Show the sender's authored inquiry as the primary message, with greeting, body, sign-off, and signature separated into readable paragraphs. Keep external-sender/security warnings, unsubscribe instructions, confidentiality notices, and link references in collapsed supporting details. Deduplicate repeated notices in the readable presentation while preserving the complete raw source. Gmail may flatten the warning and inquiry into the `Subject` value without a header/body separator; recover the body at recognized warning markers instead of showing it all as the subject.

**Why:** Real Gmail messages can contain sender warnings, opt-out text, duplicate confidentiality notices, and links inline as a dense block. A reported message also had the complete inquiry appended to `Subject`, causing the clean view to show an oversized subject and an empty body.

**How to apply:** Split recognizable sender/security markers from a flattened `Subject` before formatting the message. Use the shared Gmail body splitter for display, keep the raw email available as evidence, and do not label a notice-only email as empty when its supporting details remain available.
