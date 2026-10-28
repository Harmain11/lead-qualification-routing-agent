---
name: Gmail message presentation
description: Keep incoming Gmail inquiries readable while preserving supporting warnings and footer evidence.
---

Show the sender's authored inquiry as the primary message, with greeting, body, sign-off, and signature separated into readable paragraphs. Keep external-sender/security warnings, unsubscribe instructions, confidentiality notices, and link references in collapsed supporting details. Deduplicate repeated notices in the readable presentation while preserving the complete raw source. Gmail may flatten the warning and inquiry into `Subject`, or append the greeting and body to a valid `Date`; recover the body instead of displaying it as metadata.

**Why:** Real Gmail messages can contain sender warnings, opt-out text, duplicate confidentiality notices, and links inline as a dense block. Flattened `Subject` content can produce an oversized subject and empty body; body text appended to `Date` can produce an oversized received timestamp and empty body. macOS Safari may reject valid RFC email dates such as `+0000` when native date parsing is used, leaving the greeting in the timestamp and the inquiry body empty.

**How to apply:** Split recognizable sender/security markers from a flattened `Subject` before formatting. For `Date`, recover text only when the prefix is a valid date and the suffix starts with a recognized greeting. Parse RFC email dates through normalized ISO timestamps instead of relying solely on browser-native parsing, and use the same parser for display; otherwise preserve the original value. Use the shared Gmail body splitter for display, keep the raw email available as evidence, and do not label a notice-only email as empty when supporting details remain available.

Group messages into conversations by Gmail's actual thread ID, not by subject alone; separate Gmail threads can share a subject. When refreshing Gmail history, update thread IDs on already-imported messages as well as adding new ones, and accept the legacy thread-ID field when displaying or replying.

**Why:** The user reported messages from one Gmail conversation appearing as separate one-email cards because existing history could lack or retain an outdated thread ID.

**How to apply:** Use the canonical thread ID returned by Gmail for every message in that conversation. Preserve distinct thread groups even when subjects match, and let a user-requested refresh repair legacy message metadata without duplicating messages.

After Gmail reconnects, the same message may be present under different connection IDs. Treat a message as unique by normalized mailbox identity plus Gmail message ID, not connection ID alone; preserve historical rows while hiding repeated copies in the UI.

**Why:** Reauthorization can create a new provider-connection record while imported history retains the old ID, making one mailbox conversation appear twice.

**How to apply:** Resolve mailbox identity from verified connection metadata where possible, deduplicate display/imports by mailbox and message ID, and keep the original raw events for history and auditability.
