# Corral UI review — 12 October 2026

The UI uses one shared page frame, heading scale, control scale, spacing rhythm,
and light/dark palette across the existing product screens and operations screens.

## Fixed

- Removed oversized marketing headings from product pages and excessive header space.
- Unified card surfaces, borders, padding, typography, primary/secondary buttons,
  form controls, toolbars and page actions. Removed decorative background effects
  and card motion that distracted from operational content.
- Collapsed dashboard/analytics customization into an accessible details control;
  selections and the existing preference persistence remain unchanged.
- Fixed inherited pipeline-metric colors, chart colors, ICP setup contrast and small helper text.
- Made lead tables, integrations, settings and ICP reflow against the actual content
  column width beside navigation. Tables scroll within their own region when needed.
- Added clear empty-state messages to run history and advanced analytics tables,
  aligned pagination controls, and enabled keyboard access to scrollable tables.
- Reused the native dialog lifecycle for lead details, exports, invitations,
  staff access codes, confirmations, OpenAI setup and ICP publication.
  Tab/Shift+Tab wrap inside dialogs; Escape closes them and restores focus.
- Mobile navigation moves focus into the menu, prevents background interaction,
  wraps keyboard focus and restores the opener on Escape.
- Replaced the text G with Google's official full-color logo asset, served locally.
  The Google button uses a white surface, correct icon proportions, consistent
  padding and readable text in either Corral theme. OAuth behavior is unchanged.

## Verification

The 17 product screens were reviewed at 390 px, 768 px and 1440 px in light and dark:
Dashboard, Leads, Contacts, Companies, Pipeline, AI Activity, Intelligence,
Sequences/Playbooks, Analytics, Manual Review, Integrations, Users, Team Policies,
Compliance, Agency/Branding, Settings and Ideal Customer Profile.

Screen checks found no page-level horizontal overflow, unlabeled visible form
fields, tiny visible action labels or API alerts. Computed text-color checks on
solid backgrounds were combined with visual review of cards, charts and gradients.
This is a practical browser review, not a formal accessibility certification.

Additional checks:

- 320 px sign-in and playbook layouts: no page overflow; controls remain labelled.
- Sign-in, create-account and staff-access modes at 320/390/1440 px.
- Google logo loaded from the local asset in sign-in and create-account modes;
  the Google button retains readable contrast in dark mode.
- Invitation: forward/backward focus wrapping, Escape and opener restoration.
- Lead details, export and contact/company editors: native modal focus, labels and dismissal.
- Playbook: labelled fields, constrained canvas scrolling and 20 px keyboard movement.
- Command palette: Ctrl+K, filtering, Escape and focus restoration.
- Mobile navigation: keyboard wrapping, background exclusion and Escape restoration.
- Final TypeScript validation and Vite production build passed.
- Git whitespace validation passed.

QA used the existing illustrative workspace in a separate browser session.
No leads, contact/company fields, policies or provider credentials were saved.
Live Google OAuth, credential entry, email delivery, destructive actions and
marketplace/provider transactions were not exercised. Backend code was not changed
in this UI pass; the earlier backend regression results remain in FEATURE-STATUS.md.

## Shared files

- frontend/src/designTokens.css: theme palette, radius and spacing tokens.
- frontend/src/components/layoutConsistency.css: shared geometry and responsive rules.
- frontend/src/components/brandButtons.css: shared button states and sizing.
- frontend/src/components/focusTrap.ts and OperationsDialog.tsx: keyboard/modal behavior.
- frontend/public/asset/brand/google-g.png: official Google asset.

Google asset source: https://developers.google.com/static/identity/images/g-logo.png
Google button reference: https://developers.google.com/identity/branding-guidelines

## Readability and integration follow-up

The later readability pass removes the old 0.75em rule that shrank buttons and
labels, raises small CSS and embedded-component text to a 13 px minimum, and uses
16 px body/input text, 15 px navigation/action/table text and 14 px form labels.
The larger scale was checked against the responsive layouts. Theme changes apply immediately so an animation cannot intercept navigation clicks.

The Zapier SDK package tests (4) and the isolated Corral connector API test (1)
pass on 12 October. Account, installation and live-deployment requirements are
clarified in integrations/README.md and in the Agency/Branding connection section.


## Screenshot-driven final pass — 12 October 2026

The screenshots supplied by the user were used as concrete failure cases:

- Export and Submit lead share equal width and height (164 × 44 px on desktop).
- Dashboard peer cards use equal columns and stretch to matching row heights.
- Source insight headers use consistent title/description rows; their empty frames align.
- Empty lead subpages have a padded state and omit the empty table header.
- Sidebar keyboard focus rings remain within the scrolling navigation frame.
- Body text, labels, controls, checkboxes and textareas use the shared readable scale.
- Compact native export/invitation/record dialogs preserve focus trapping and restoration.
- Search Corral uses a centered 640 px command window, fixed metadata/label columns,
  15 px result labels, bounded vertical scrolling and Arrow/Enter/Tab/Escape navigation.
  Mobile results have no internal horizontal scrolling. Actual lead search opens the
  correct existing record.
- Lead details use one content edge, separated cards, aligned header actions and responsive
  tab grids. Qualification status remains visible on mobile. Inquiry, AI, follow-up,
  closing and history sections were all checked.
- All six new provider forms have visible labels, 44 px fields and masked secret inputs.
  Connect focuses credential setup; it does not collapse the form. Store remains disabled
  until all required fields are populated.

The final main-route audit records 64 checks: 16 routes × light/dark × 390/1440 px.
No page overflow, clipped action labels, action heights below 43 px, text below 13 px
or API alerts were found in that set. Earlier 768/1024 checks and 46 inner-page checks
are retained as additional evidence. Final lead/search/integration checks were repeated
as those surfaces changed. This is practical visual and keyboard QA, not certification.

Evidence is saved in the task visualization directory: corral-final-layout-audit.json,
corral-search-desktop-final.jpg, corral-search-mobile-final.jpg,
corral-lead-detail-final.jpg, corral-lead-mobile-final.jpg and the provider-form audit.
No customer data, credentials or external transactions were saved during browser QA.
The illustrative workspace's normal viewing-presence heartbeat was exercised.

Final expanded-lead coverage records 22 checks across all five tabs, both themes,
390/1440 px and tablet overview checks at 768/1024 px. Both lead overview cards
measure 548 × 447.85 px at 1440 px and start on the same baseline. All 12 new
provider form checks (six providers × 390/1440 px) pass. The final backend-focused
suite passes 29 tests; the production frontend build and whitespace/lint checks pass.
