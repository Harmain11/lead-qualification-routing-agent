---
name: Meeting scheduling safety
description: Product requirements for Gmail-based meeting recommendations in Corral.
---

Read the tracked Gmail conversation in the correct workspace and Gmail mailbox. A date-only request gets open options for that date. An exact future date and time may be booked only after the connected calendar confirms it is free; if it is not free, email available options and book only after the lead clearly selects one. Do not book ambiguous replies, and keep the existing manual booking path.

**Why:** The user clarified that an exact date/time can be booked automatically when available, while date-only requests must get options and unavailable times must fall back to options.

**How to apply:** Interpret requested times in the connected calendar’s timezone. Use the lead’s exact Gmail connection, thread, and workspace when routing messages and offers. Send options in that same thread; accept only a clear numbered selection for offered slots. Keep ambiguous replies and requests for different times visible for team follow-up.

Calendly account and event-type reads can succeed even when an event type’s linked external calendar is unavailable. Skip only that event type when other Calendly event types can still provide slots; if none work, direct the user to reconnect the calendar inside Calendly rather than reauthorizing Corral’s Calendly connection.

**Why:** Calendly returned a specific external-calendar failure from availability lookup while its OAuth identity and event-type endpoints remained accessible.

**How to apply:** Treat this error as a Calendly-side calendar connection issue. Preserve results from other event types and show a clear reconnect instruction only when the affected provider cannot offer alternatives.

Calendly's connected external calendars are account-level settings shared by its event types; a separate calendar cannot be assigned to each event type. Corral can show and filter event types with usable slots, but calendar reconnect/selection must happen in Calendly. Creating an event type does not repair a disconnected calendar.

**Why:** Calendly's current help center states connected calendars apply to all event types and documents reconnecting/connecting calendars separately from event type setup.

**How to apply:** Distinguish choosing a working event type from choosing the underlying external calendar. When no Calendly type has availability, guide users to reconnect or connect a calendar first; offer event-type setup steps only as a separate remedy.
