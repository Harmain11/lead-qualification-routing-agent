# Corral Create Lead connectors

These are development packages, not published marketplace listings. The connection is a
workspace-specific intake capability; it cannot list or edit existing leads. Treat its token
as a password. Rotation in Corral Integrations revokes both connectors.

Use the existing **Website form / public intake URL** from Integrations:
https://YOUR_HOST/v1/intake/public/TOKEN. Split it into HTTPS origin/hostname and token.
Use a publicly reachable HTTPS deployment. localhost is for browser preview only.
Map a stable external_id from the originating system so delivery retries are idempotent.
Lead qualification, routing and existing confirmation settings use the canonical intake
pipeline. Scheduled follow-up sequences still prepare drafts for rep approval.

## Zapier
From integrations/zapier, run npm ci and npm test.
Install the official Zapier Platform CLI, log into the owning vendor account, then
register, validate and push this package. Configure the two auth fields and test Create Lead
in a test workspace. Submission, listing assets and public review still need the vendor account.

## Make
Create a private custom app in the owning Make account. Add a basic connection, pasting
connection-parameters.json and connection-communication.json into the corresponding editors.
Add a **Create Lead** action using this connection; paste the parameter, communication and
interface files into their matching editors. Test with a test workspace and a stable source ID.
Install/share the private app before requesting marketplace review. These are editor components,
not a fabricated published app ID or an exported Make scenario.

## Validation and hosting
The connection check validates token, active workspace and residency before returning only
workspace ID/name. It does not create a lead. Server application logs redact intake tokens.
Configure reverse-proxy/access logs to redact /v1/intake/public/* too; local preview uses
--no-access-log. No real source credentials or contact records are in these packages.

Official references:
- https://github.com/zapier/zapier-platform/blob/main/packages/cli/docs/cli.md
- https://developers.make.com/custom-apps-documentation/app-components/connections/basic-connection
- https://developers.make.com/custom-apps-documentation/app-components/modules/action

## Account setup and live readiness — verified 12 October 2026

The token-based Corral connectors do not require a Corral OAuth client. You can use
an existing Zapier/Make account; custom integrations still need registration and
installation in the corresponding platform. This is separate from publishing a
marketplace listing. A private app can be used before marketplace approval.

If you use built-in HTTP/webhook modules, no custom Corral app registration is
needed. You still need a publicly reachable HTTPS Corral deployment, an active
workspace intake token and access to those modules under the vendor's account plan.
The new Corral branch has not been deployed or published to either marketplace.

Rechecked: four Zapier tests, including the official SDK schema, and the isolated
Corral connection test covering token validation, tenant/residency boundaries and
log redaction. Make's connection expression and communication structure match the
official basic-connection documentation. Live scenarios, TLS/reverse-proxy behavior
and vendor installations must still be tested against the actual public deployment.

Google sign-in is a separate integration: production needs GOOGLE_CLIENT_ID,
GOOGLE_CLIENT_SECRET and the corresponding authorized origins/redirect URLs.
The local preview uses the development OAuth broker.

References:
- https://github.com/zapier/zapier-platform/blob/main/packages/cli/docs/cli.md
- https://www.make.com/en/how-to-guides/build-custom-app-in-make
- https://developers.make.com/custom-apps-documentation/create-your-first-app/app-visibility


## GoHighLevel, dialers and voice agents

Workspace setup and capability limits for GoHighLevel, Twilio, Aircall, RingCentral,
Vapi and Retell AI are documented in [CALLING.md](CALLING.md). These are independent
of the Zapier/Make custom-app registration and marketplace-publication steps above.
