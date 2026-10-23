# Phase 7C4A: Slack preparation for live certification

Slack is configured as an outbound notification destination. It is not an inbound lead source and it has no CRM synchronization controls or schedule.

## Implementation status and deferred acceptance

The Slack provider implementation is operational. Both deferred live acceptance checks
were completed in Phase 7C8 on 2026-09-26 using automatic scheduled Gmail inbound:

1. A meaningful Gmail thread update invoked the existing
   `reprocess_lead()` path, creates the appropriate new routing decision, and produces
   exactly one legitimate second Slack notification. The next unchanged scheduled cycle
   produced no duplicate lead processing, routing decision or notification.
2. That real Slack notification generated the post-fix correlated
   `lead_notification` lifecycle audit event, with no secret leakage. Safe evidence IDs
   and timestamps are recorded in `phase-7c-provider-certification.md`.

Do not add a manual reprocess UI solely for certification. The existing ingestion and
reprocessing behavior is the path under acceptance; the checks above record the
notification behavior at that time.

## Current one-notification-per-lead policy

The Phase 7C8 second-notification check above records historical behavior. The current
product policy, confirmed 2026-10-19, suppresses any additional Slack alert for a lead
when an earlier alert is pending, retryable, or sent. Retry the original notification
record instead of creating another alert through a later routing decision.

## Slack app setup for the next live phase

1. Create a Slack app **from scratch** for a dedicated test workspace. Keep it a workspace-installed app for this certification; do not enable organization-wide distribution.
2. In the application, the operator must have both `integrations.connect` and `integrations.manage` to start Slack OAuth. Channel selection, verification, and the test notification require `integrations.manage`; channel changes are workspace-scoped.
3. Under **OAuth & Permissions**, add this redirect URL exactly, substituting the deployed public API origin:

   ```text
   {INTEGRATION_PUBLIC_BASE_URL}/v1/integrations/slack/oauth/callback
   ```

   The callback path comes from `begin_oauth_connection` and the browser callback route in `backend/src/lead_agent/api/routes/integrations.py`. The service sends the configured `INTEGRATION_PUBLIC_BASE_URL` plus `/v1/integrations/slack/oauth/callback`.

4. Add these **Bot Token Scopes**, with no user token scopes:

   ```text
   chat:write
   channels:read
   ```

   `chat:write` sends the notification. `channels:read` lists public channels for the workspace-scoped channel picker and verifies the chosen channel. Private-channel history, message history, user, admin, and broad workspace scopes are not requested.
5. Install the app to the test workspace. Invite the bot to a public test channel before choosing it in the Integration Center. The picker lists public channels where this bot is already a member. The chosen stable channel ID is saved against that Slack connection.
6. Configure the application operator credentials on the API deployment:

   ```text
   OAUTH_BROKER=provider
   SLACK_CLIENT_ID=...
   SLACK_CLIENT_SECRET=...
   INTEGRATION_PUBLIC_BASE_URL=https://<public-api-host>
   FRONTEND_PUBLIC_BASE_URL=https://<public-app-host>
   NOTIFICATION_PROVIDER=slack
   SECRET_STORE_PROVIDER=managed
   MANAGED_SECRET_STORE_BACKEND=<configured-secret-adapter>
   ```

   `SLACK_CLIENT_ID` and `SLACK_CLIENT_SECRET` belong to the operator-owned Slack app and stay in server-side deployment secrets. The workspace bot access token is returned for each installation and stored encrypted through `SecretStore`; it is never configured as an operator-wide token or returned to the browser. Workspace channel IDs are connection metadata and never come from global deployment configuration.

## Certification sequence

1. Start OAuth from the Slack Integration card and install into the dedicated test workspace.
2. Use **Verify connection**. The application calls `auth.test`, checks the token's granted scopes and confirms the returned team ID matches the team ID from the OAuth exchange.
3. Invite the bot to the test channel, load the public channel list, and choose the destination. Confirm the card shows the workspace and channel.
4. Use **Send test notification** after explicitly requesting it in the UI. Confirm the clearly labelled test message reaches that channel and that an audit event is recorded.
5. Process one controlled test lead. Confirm one concise notification is delivered, reprocessing the unchanged lead does not send another, and Slack cannot import users/messages or enter CRM synchronization.
6. Disconnect from the Integration card. Confirm the local workspace connection is disabled even if Slack's token revoke call is unavailable, and repeat the same checks in a second workspace to confirm isolation.

No Slack workspace was opened or contacted during the Phase 7C4A preparation. The
provider implementation is operational; the two live end-to-end acceptance checks
listed above are now complete. Historical setup instructions remain for reference.
