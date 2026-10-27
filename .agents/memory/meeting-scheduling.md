---
name: Meeting scheduling safety
description: Product requirements for Gmail-based meeting recommendations in Corral.
---

Read the tracked Gmail conversation in the correct workspace and Gmail mailbox. A date-only request gets open options for that date. Directly book an exact future time only when the contact's timezone is explicit or can be unambiguously derived from a validated phone number, and the connected calendar confirms availability. If the timezone is unknown, do not auto-book; show offered times in the provider timezone and ask the contact to confirm. When a reply is needed, create an editable draft in the lead's Emails tab, notify the assigned representative, and send only after that person reviews and explicitly sends it. After sending, a clear numbered choice may book automatically. Do not book ambiguous replies.

**Why:** The user confirmed that clear numbered replies should continue to book automatically; exact date/time requests may book when the contact timezone and availability are known, while date-only requests must get options. They also requested human review and clarified that the editable meeting draft belongs in the Emails tab, not Follow-up.

**How to apply:** Prefer the lead's explicit timezone, then a validated contact-authored phone number only when it resolves to one timezone. Do not infer timezone from an email address or domain, and do not take a lead's phone from another participant's signature or quoted text. Use the exact Gmail connection, thread, and workspace for the saved draft and its later send. Display and edit meeting drafts in Emails, not Follow-up. Make the displayed timezone clear when the contact zone is unknown; accept only a clear numbered selection for offered slots. Keep ambiguous replies and requests for different times visible for team follow-up.

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
