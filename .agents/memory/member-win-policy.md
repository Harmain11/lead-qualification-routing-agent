---
name: Member performance and successful conversions
description: Product decisions for comparing members and recording Project/Sale wins.
---

Users choose Project or Sale for each successful conversion; Lost remains a separate outcome.

**Why:** The user selected per-win choice rather than a workspace-wide default.

**How to apply:** Treat these as commercial facts recorded in Corral, not inferred from an AI score. Recording one must not silently create a Salesforce or HubSpot object.

Manual closing and comments must be visible and easy to find.

**Why:** The user clarified “Make manual closing and comments visible”; they did not ask to remove those controls.

**How to apply:** Preserve the working closing/comment form when simplifying lead details, and prioritize it over the long decision trail.

Before saving narrative text in the Status form, ask whether it is a status update or a regular comment. Status updates use the progress workflow; regular comments are saved as team notes without changing sales status.

**Why:** The user asked Corral to distinguish an update from a regular comment before saving.

**How to apply:** Present the intent choice before the text submission, then use the matching progress or team-note save path.

Show the AI's buying-stage assessment beside manual closing, without treating it as a confirmed commercial outcome.

**Why:** The user asked to show what AI thinks the lead's stage is in the closing section. A buying-stage estimate can be inferred or older than the latest recorded outcome.

**How to apply:** Use the latest available assessment with its uncertainty and age; show unknown/unavailable states explicitly. AI stage estimates must not automatically close a lead or overwrite its recorded outcome.

Congratulate both the team channel and the credited member personally.

**Why:** The user selected “Team channel and assigned member.”

**How to apply:** Use workspace-scoped member identities and an explicit Slack member mapping, not matching display names. Report channel and personal delivery independently. Notification failures must not undo the recorded commercial outcome.
