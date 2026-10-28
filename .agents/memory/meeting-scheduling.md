---
name: Meeting scheduling safety
description: Product requirements for Gmail-based meeting recommendations in Corral.
---

Read the tracked Gmail conversation in the correct workspace and Gmail mailbox. Date-only and exact date/time requests both create an editable booking-link draft; do not automatically book from a meeting-request email or a newly proposed time. When a reply is needed, create the draft in the lead's Emails tab, notify the assigned representative, and send only after that person reviews and explicitly sends it. After sending, use the workspace's configured reasoning provider to interpret natural replies. Book only a high-confidence confirmation tied to exactly one offered slot; a single-option offer may accept clear agreement, while multiple options require a clear ordinal or unique date/time reference. Ambiguous, tentative, mixed, declined, invalid, or unclassifiable replies must not book. Preserve explicit numbered-choice handling when AI is unavailable, and cache each message's interpretation to avoid repeat calls.

**Why:** The user wants exact-time requests and proposed alternate times to remain drafts for human review; only a clear confirmation of a time already offered may book automatically. They also requested AI understanding of natural replies such as ordinal choices and clarified that editable meeting drafts belong in Emails, not Follow-up.

**How to apply:** Prefer the lead's explicit timezone, then a validated contact-authored phone number only when it resolves to one timezone. Do not infer timezone from an email address or domain, and do not take a lead's phone from another participant's signature or quoted text. Use the exact Gmail connection, thread, and workspace for the saved draft and its later send. Meeting-draft notifications should open Emails. Display the timezone clearly when a time is shown. AI classifies intent only: verify that evidence is present in the reply and uniquely identifies the offered slot; never let the model itself authorize booking. Keep ambiguous replies and requests for different times visible for team follow-up.

Allow a manually selected time to replace an unbooked offer for the same request. Keep the earlier sent email in history, but supersede its booking state so replies to its old options cannot book; never replace booked or uncertain/in-progress offers.

**Why:** The user chose replacing an unbooked offer so a new selected time can produce an editable draft without allowing conflicting replies to book.

**How to apply:** Warn before replacement, create the new unsent draft in the lead's Emails tab, and keep booked or delivery/booking-uncertain states blocked.

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

For every meeting-intent email, including exact-time requests and newly proposed times in replies, prepare a personalized reply in the existing Gmail thread with a public booking link instead of booking directly or requiring the representative to choose a slot. Prefer Calendly whenever it is connected; use the workspace-configured Google Calendar appointment-schedule link only when Calendly is not connected. Keep the appointment draft editable in Emails, notify the assigned representative, and never send it automatically. After the provider confirms a unique matching booking, record the meeting on the lead and notify the assigned representative.

**Why:** The user confirmed exact-time requests should also produce a draft and notification rather than an automatic booking, and wants the representative to review, edit, and send the thread-aware booking-link draft.

**How to apply:** Keep Google booking links workspace-scoped, preserve the Emails “Appointment draft” label, route meeting-draft notifications to Emails, and only reconcile provider events that uniquely match the lead and were created after the link was sent. Do not silently replace a prior unbooked offer.

If Gmail is reconnected, a historical meeting request may still reference the previous connection ID. Reuse a current verified connection only when the Google account ID or normalized mailbox email proves it is the same mailbox; keep source-message identity separate from the connection used to send.

**Why:** Requiring an exact old connection ID can block an editable appointment draft even when that same mailbox has been reauthorized.

**How to apply:** Verify the source event in its original thread, resolve only to an active verified same-mailbox connection for sending, and keep the reconnect error when mailbox identity cannot be proven.
