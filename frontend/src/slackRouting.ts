import type { ProviderConnection, SlackNotificationRule } from "./types";
import { createClientId } from "./clientIds";

export function createSlackRule(connections: ProviderConnection[], defaultConnectionId: string | null, ruleCount: number): SlackNotificationRule {
  const verified = connections.filter((connection) => connection.status === "verified" && connection.is_active);
  const target = verified.find((connection) => connection.id === defaultConnectionId) ?? verified[0];
  if (!target) throw new Error("Connect and verify Slack above before adding a routing rule.");
  const channel = target.metadata.slack_channel_id;
  return {
    id: createClientId(),
    name: `Rule ${ruleCount + 1}`,
    connection_id: target.id,
    destination_type: "channel",
    channel_id: typeof channel === "string" && channel.trim() ? channel.trim() : null,
    queues: [],
    qualification_bands: [],
    sources: [],
    weight: 1,
  };
}
