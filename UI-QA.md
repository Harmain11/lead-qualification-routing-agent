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
