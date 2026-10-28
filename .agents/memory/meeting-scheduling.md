---
name: Meeting scheduling safety
description: Product requirements for Gmail-based meeting recommendations in Corral.
---

Read the tracked Gmail conversation in the correct workspace and Gmail mailbox. A date-only or open-ended request should prepare an editable booking-link email draft for human review, using a Calendly event-type URL or the configured Google Appointment Schedule URL. Do not send that draft automatically. An exact future date and time may still be booked only after the connected calendar confirms it is free. Keep the existing manual booking path and accept only clear numbered choices for legacy offers.

**Why:** The user chose provider-hosted booking links and editable email drafts for review; exact date/time booking remains subject to the existing availability check.

**How to apply:** Interpret requested times in the connected calendar’s timezone. Use the lead’s exact Gmail connection, thread, and workspace when routing messages and offers. Keep the provider booking link in the editable draft, require review before sending, then reconcile the resulting provider event to the lead. Keep ambiguous replies and requests for different times visible for team follow-up.

Calendly account and event-type reads can succeed even when an event type’s linked external calendar is unavailable. Skip only that event type when other Calendly event types can still provide slots; if none work, direct the user to reconnect the calendar inside Calendly rather than reauthorizing Corral’s Calendly connection.

**Why:** Calendly returned a specific external-calendar failure from availability lookup while its OAuth identity and event-type endpoints remained accessible.

**How to apply:** Treat this error as a Calendly-side calendar connection issue. Preserve results from other event types and show a clear reconnect instruction only when the affected provider cannot offer alternatives.

Calendly's connected external calendars are account-level settings shared by its event types; a separate calendar cannot be assigned to each event type. Corral can show and filter event types with usable slots, but calendar reconnect/selection must happen in Calendly. Creating an event type does not repair a disconnected calendar.

**Why:** Calendly's current help center states connected calendars apply to all event types and documents reconnecting/connecting calendars separately from event type setup.

**How to apply:** Distinguish choosing a working event type from choosing the underlying external calendar. When no Calendly type has availability, guide users to reconnect or connect a calendar first; offer event-type setup steps only as a separate remedy.

Before rejecting a Calendly booking as unavailable, check for a matching scheduled event for the same invitee, event type, and start time. Calendly may already have created it even if Corral did not record the response. Do not send a second confirmation email from Corral by default; Calendly's event-type notification settings govern its standard invite and confirmation.

**Why:** A booking can exist in Calendly after Corral reports a booking error, and duplicating Calendly's normal email can confuse the invitee.

**How to apply:** Reconcile an existing provider event before rechecking availability or retrying a booking. For a saved-choice retry, fetch its exact stored Gmail thread and pass the offer's original source message ID; current conversation evidence may point to a later reply. Detect meeting intent only in unquoted message text, and do not mistake a clear numbered choice for a new request because its subject contains meeting language. Preserve replies that ask for different times. Show provider errors and direct missing-email issues to Calendly's event-type notification settings rather than sending another message.

Direct Calendly bookings must include the selected event type's configured location when it has one. Do not guess when there are multiple location choices or the invitee must supply location details.

**Why:** On 2026-10-05, the user confirmed bookings worked after matching the invitee request's location to the selected Calendly event type; omitting it caused Calendly to reject the booking.

**How to apply:** Derive the location from a fresh event-type response, include it in the invitee request, and keep regression coverage for that mapping. Calendly remains responsible for its standard confirmation email.
