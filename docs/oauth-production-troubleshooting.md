# Production OAuth troubleshooting

## Slack workspace restriction

`invalid_team_for_non_distributed_app` means the selected workspace is not the
workspace where the Slack app was created and Slack App Distribution is disabled.
Corral cannot override this provider restriction.

For a single-workspace test, select the app's original workspace. For installation
in other workspaces, the Slack app owner must open the app dashboard, go to
**Manage Distribution → Share Your App with Other Workspaces**, complete the
checklist and select **Activate Public Distribution**. This is separate from
Enterprise organization-wide deployment. Workspace administrators may still need
to approve the app.

Register the exact public API callback:
`{INTEGRATION_PUBLIC_BASE_URL}/v1/integrations/slack/oauth/callback`.
Keep the bot scopes `chat:write` and `channels:read`.

Official instructions: https://docs.slack.dev/app-management/distribution

## Salesforce authorization

Register the exact public API callback:
`{INTEGRATION_PUBLIC_BASE_URL}/v1/integrations/salesforce/oauth/callback`.
Enable authorization-code flow, S256 PKCE, `api` and `refresh_token` scopes.
Ensure the user can self-authorize or has the administrator-approved assignment
required by the Salesforce app's policy. Developer Edition uses
`login.salesforce.com`; Salesforce sandboxes use `test.salesforce.com`.

Use the recorded callback reason to distinguish provider denial, invalid local
state, expired transactions and token exchange rejection. The application logs
only allowlisted provider error codes, not descriptions, codes, state or tokens.
Unknown provider error values are replaced by `oauth_provider_error`.

Salesforce also returns provider-specific codes such as `invalid_app_access`,
`inactive_user`, `inactive_org` and `CSRF`; these must not be confused with an
internal server error. Corral displays static, code-specific guidance for these
documented errors, without exposing provider descriptions or credentials.

For `invalid_app_access`, have the Salesforce administrator inspect the app's
**Permitted Users** policy. If it is **Admin approved users are pre-authorized**,
assign the connecting user through the app's permitted profiles or permission
sets. Keep the existing policy unless the administrator deliberately chooses to
change it; do not turn off preauthorization as a workaround.

An already-saved `oauth_provider_error` cannot be reclassified: the original
unrecognized code was discarded. Start a new connection after publishing the
expanded diagnostics to obtain the specific safe reason.

Official error-code reference:
https://help.salesforce.com/s/articleView?id=xcloud.remoteaccess_oauth_flow_errors.htm&language=en_US&type=5

A background worker's successful token refresh or CRM query does not prove a new
browser authorization succeeded. Check callback diagnostics and the specific
connection's state. Old attempts recorded only `authorization_pending`; their
provider rejection reasons cannot be reconstructed retrospectively.

## Applying a fix

Workspace code changes do not change the running production build until Publish.
Start a new authorization after publishing; do not reuse an old OAuth callback.
Do not disable state checks, actor binding, PKCE, or app authentication to debug.
