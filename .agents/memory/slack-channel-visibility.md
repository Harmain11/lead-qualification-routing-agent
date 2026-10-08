---
name: Slack channel visibility
description: Distinguish discoverable Slack channels from joined channels and active connections.
---

The user's Slack workspace has channels even when Corral showed an empty
channel list. Show discoverable public channels before the app joins them,
mark invitation status, and do not display disconnected or inactive
connections as live routing destinations.

**Why:** The user confirmed Slack already has channels. Previously the app
filtered the channel list by bot membership, and verified-but-inactive
historical connections looked active after disconnecting.

**How to apply:** The user wants channel selection to add the app where
possible. Join only the explicitly selected public channel with Slack's
channels:join permission and confirm membership before saving a destination.
Private channels require a human invitation; groups:read only reveals private
channels already accessible to the app. Never promise to auto-join every
private channel. Preserve historical connections without presenting them as active.

Keep new channel permissions opt-in through authorization rather than
invalidating existing working Slack connections.

**Why:** Automatic public-channel joining and invited-private-channel listing
need additional provider consent. A release must not treat a previously valid
notification token as broken merely because newer optional features need more permissions.

**How to apply:** Request the extra permissions on connection/reconnection,
keep the original notification permissions as the verification minimum, and
explain reconnection when a user selects a feature their existing token cannot perform.
