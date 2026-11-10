import type { WorkspaceAssistantArea } from "../api";

/**
 * Commands Ask Corral runs instantly in the browser, with no model round-trip.
 * Only safe, reversible UI moves live here: opening a page, closing the panel
 * and starting a new chat. Anything that changes data still goes through the
 * agent and its review card.
 */
export type AssistantCommand =
  | { kind: "navigate"; area: WorkspaceAssistantArea }
  | { kind: "close" }
  | { kind: "new_chat" };

const AREA_ALIASES: Record<WorkspaceAssistantArea, string[]> = {
  dashboard: ["today", "dashboard", "home", "overview"],
  leads: ["leads", "lead list", "all leads"],
  pipeline: ["pipeline", "board", "pipeline board", "deals"],
  revenue: ["revenue", "forecast", "revenue forecast"],
  review: ["review", "reviews", "manual review", "review queue"],
  email: ["inbox", "email", "emails", "mail", "mailbox", "gmail"],
  calendar: ["calendar", "meetings", "schedule", "agenda"],
  dialer: ["calls", "call history", "call log", "dialer", "phone"],
  contacts: ["contacts", "people"],
  accounts: ["companies", "accounts", "organisations", "organizations"],
  performance: ["team performance", "performance", "leaderboard"],
  analytics: ["analytics", "reports", "reporting", "insights"],
  agents: ["ai activity", "agent activity", "agents", "agent log"],
  intelligence: ["research", "coaching", "research and coaching", "research & coaching", "intelligence"],
  automations: ["automations", "automation", "sequences", "playbooks", "workflows"],
  integrations: ["integrations", "connections", "apps"],
  team: ["team", "members", "users", "teammates"],
  settings: ["settings", "preferences", "workspace settings"],
  icp: ["icp", "ideal customer profile", "ideal customer"],
  policies: ["routing", "routing and capacity", "routing & capacity", "policies", "capacity"],
  compliance: ["compliance", "consent", "governance", "consent and governance", "consent & governance"],
};

const ALIAS_TO_AREA = new Map<string, WorkspaceAssistantArea>(
  (Object.entries(AREA_ALIASES) as Array<[WorkspaceAssistantArea, string[]]>)
    .flatMap(([area, aliases]) => aliases.map((alias) => [alias, area] as const)),
);

const NAVIGATE = /^(?:open|go to|goto|show me|show|take me to|navigate to|switch to|bring up|jump to|view)\s+(?:the\s+|my\s+)?(.+)$/;
const CLOSE = /^(?:close|hide|dismiss|exit|minimi[sz]e)(?:\s+(?:the\s+)?(?:assistant|chat|agent|corral|panel|window|ask corral))?$/;
const NEW_CHAT = /^(?:new|start (?:a )?new|clear(?: the)?)\s+(?:chat|conversation|thread)$/;

function normalize(text: string) {
  return text
    .toLowerCase()
    .replace(/[.!?]+$/g, "")
    .replace(/^(?:please|pls|can you|could you|hey corral,?|corral,?)\s+/g, "")
    .replace(/\s+please$/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function areaFor(target: string): WorkspaceAssistantArea | null {
  const cleaned = target.replace(/\s+(?:page|tab|view|screen|section)$/, "").trim();
  return ALIAS_TO_AREA.get(cleaned) ?? null;
}

export function parseAssistantCommand(text: string): AssistantCommand | null {
  const input = normalize(text);
  if (!input || input.length > 60) return null;
  if (CLOSE.test(input)) return { kind: "close" };
  if (NEW_CHAT.test(input)) return { kind: "new_chat" };
  const match = NAVIGATE.exec(input);
  const area = areaFor(match ? match[1] : input);
  return area ? { kind: "navigate", area } : null;
}
