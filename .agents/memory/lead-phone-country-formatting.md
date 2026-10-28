---
name: Lead phone country formatting
description: Rules for converting a lead's local phone number to an international dialing format.
---

For lead calls, use a clearly identified country from the lead's Geography to format a local number. Preserve numbers that already include `+country code`. If no country is identifiable, only add `+1` to a valid North American Numbering Plan number; if the country or number is ambiguous, require an explicit international number rather than guessing.

**Why:** The user chose country-based phone formatting after a locally formatted lead number was rejected during a CloudTalk handoff, and approved `+1` only for valid NANP numbers when country information is unavailable.

**How to apply:** Keep this behavior scoped to lead call targets. Manual direct dialing remains explicit and international; do not infer its country from workspace defaults or caller identity.
