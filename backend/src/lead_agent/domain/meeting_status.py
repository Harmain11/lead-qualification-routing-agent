"""Statuses that mean a calendar provider actually confirmed a meeting."""

# "scheduled" is what booking writes today; the others are accepted from older records.
VERIFIED_MEETING_STATUSES: frozenset[str] = frozenset({"scheduled", "meeting_scheduled", "booked", "confirmed"})
