import { useState } from "react";
import type { SlackChannel, SlackMember } from "../api";
import type { ProviderConnection } from "../types";

type Props = {
  connection: ProviderConnection;
  channels: SlackChannel[] | undefined;
  members: SlackMember[];
  canManage: boolean;
  busy: boolean;
  onRefresh: () => void;
  onSelect: (id: string) => void;
  onTest: () => void;
  onMembers: () => void;
  onReconnect: () => void;
};

export function SlackChannelDestination({
  connection, channels, members, canManage, busy,
  onRefresh, onSelect, onTest, onMembers, onReconnect,
}: Props) {
  const [privateHelp, setPrivateHelp] = useState(false);
  const channelId = typeof connection.metadata.slack_channel_id === "string" ? connection.metadata.slack_channel_id : "";
  const channelName = String(connection.metadata.slack_channel_name || channelId);
  const granted = Array.isArray(connection.metadata.granted_scopes)
    ? connection.metadata.granted_scopes : connection.scopes;
  const canJoin = granted.includes("channels:join");
  const canListPrivate = granted.includes("groups:read");
  const teamId = typeof connection.metadata.slack_team_id === "string" ? connection.metadata.slack_team_id : "";

  return <div className="integration-guide slack-destination">
    <strong>Connected — Verified</strong>
    <span>Workspace: {connection.account_name}</span>
    <span>Notification channel: {channelId ? `#${channelName}` : "Choose a channel"}</span>
    {canManage && <>
      <p>{canJoin
        ? "Choose a public channel below and Corral will join it automatically before saving it as your notification destination."
        : "Reconnect Slack once to enable automatic public-channel joining. You can also invite the connected app in Slack and select its channel here."}</p>
      {(!canJoin || !canListPrivate) && <button className="button-quiet" onClick={onReconnect} disabled={busy}>Enable channel permissions</button>}
      <div className="card-actions">
        <button className="button-quiet" onClick={onRefresh} disabled={busy}>Refresh channels</button>
        <button className="button-quiet" onClick={() => setPrivateHelp((open) => !open)} aria-expanded={privateHelp}>Add a private channel</button>
        {channelId && <>
          <button className="button-quiet" onClick={onTest} disabled={busy}>Send test notification</button>
          <button className="button-quiet" onClick={onMembers} disabled={busy}>View channel members</button>
        </>}
      </div>
      {privateHelp && <div className="notice" role="note">
        <strong>Private channels require an invitation in Slack</strong>
        <p>Open the private channel in Slack, click its name, then choose Integrations › Add apps and add the connected app. Slack does not allow apps to discover or join private channels without an invitation.</p>
        {!canListPrivate && <p>First use Enable channel permissions and approve the private-channel listing permission. Existing notifications keep working until you reconnect.</p>}
        {teamId && <a className="button-quiet" href={`https://app.slack.com/client/${encodeURIComponent(teamId)}`} target="_blank" rel="noreferrer">Open workspace in Slack</a>}
        <p>After inviting the app, press Refresh channels. The private channel will appear here for selection.</p>
      </div>}
      {channels && channels.length === 0 && <p role="status">Slack returned no accessible channels. Public channels should appear here; private channels appear only after the app is invited and the listing permission is granted.</p>}
      {channels && channels.length > 0 && <label>Notification channel
        <select value={channelId} disabled={busy} onChange={(event) => { if (event.target.value) onSelect(event.target.value); }}>
          <option value="">Select a Slack channel</option>
          {channels.map((channel) => <option value={channel.id} key={channel.id}>
            #{channel.name}{channel.is_private ? " · private" : ""}{channel.is_member ? "" : canJoin && !channel.is_private ? " · join automatically" : " · invite app first"}
          </option>)}
        </select>
      </label>}
      {members.length > 0 && <div className="slack-member-list"><small>Members in this channel</small>{members.map((member) => <span key={member.id}>{member.name} · {member.id}</span>)}</div>}
    </>}
  </div>;
}
