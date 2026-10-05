---
name: Meeting scheduling safety
description: Product requirements for Gmail-based meeting recommendations in Corral.
---

Read the tracked Gmail conversation in the correct workspace and Gmail mailbox. A date-only request gets open options for that date. An exact future date and time may be booked only after the connected calendar confirms it is free; if it is not free, email available options and book only after the lead clearly selects one. Do not book ambiguous replies, and keep the existing manual booking path.

**Why:** The user clarified that an exact date/time can be booked automatically when available, while date-only requests must get options and unavailable times must fall back to options.

**How to apply:** Interpret requested times in the connected calendar’s timezone. Use the lead’s exact Gmail connection, thread, and workspace when routing messages and offers. Send options in that same thread; accept only a clear numbered selection for offered slots. Keep ambiguous replies and requests for different times visible for team follow-up.
