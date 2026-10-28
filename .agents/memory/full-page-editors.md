---
name: Full-page menus and readable formatting
description: User direction for Corral editor widths, ornamental lines, and text spacing.
---

Use full-page menus and detail/editor surfaces rather than half-page drawers.

**Why:** The user asked, “Make all half page menus full page and fix spacing and formatting issues like overlapping line spacing etc.”

**How to apply:** Keep close/back controls accessible and verify desktop and phone layouts, including wrapping and vertical spacing.

Do not reintroduce the decorative concentric AI diagram or gradient decorative lines.

**Why:** The user asked to remove the illustrated AI diagram and “Remove the gradient lines throughout.”

**How to apply:** Prefer ordinary dividers and meaningful content rather than decorative connectors or gradient outlines.

Preserve the complete lead information and actions when splitting the detail view into directly accessible, pinnable sections.

**Why:** The user requested lead subcategories for easier navigation, explicitly said “lead all info should be there,” and asked for pinning like HubSpot.

**How to apply:** Group information rather than removing it. Keep permission checks and preserve unsaved form state when changing sections.

From the lead header's action menu, make the full set of editable company and contact details easy to reach, rather than exposing only a single field such as job title.

**Why:** The user asked to edit the company name and other lead details easily from the record.

**How to apply:** Keep one clear Edit lead details action and preserve source inquiry text, permission gates, and CRM-sync confirmation.

Show navigation subcategories when the cursor is on the primary navigation item, with equivalent tap and keyboard access. For Integrations, close the provider pane when the pointer returns from it to the same category, while keeping the category column open; hovering a different category switches the pane. Keep the current page and subsection when refreshing.

**Why:** The user asked for hover subcategories and reported that refresh always returned them to the home dashboard.

**How to apply:** Preserve these behaviors during future navigation or layout changes; distinguish a return from the provider pane from a first hover over a category, and do not require hover on phones.

Long integration provider details must use the page's normal scroll rather than a capped nested scroll area.

**Why:** The user reported that scrolling through integration details became stuck on a Mac.

**How to apply:** Keep expanded provider content at its natural height and avoid `max-height`, `overflow: auto`, or overscroll containment on the details wrapper. Reserve nested scrolling for genuinely bounded lists.
