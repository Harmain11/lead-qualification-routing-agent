import { useState } from "react";
import { Icon } from "../v3/ui/Icon";
import type { WorkspaceAssistantArea } from "../api";
import { ASSISTANT_AREAS, canOpenAssistantArea } from "./workspaceAssistantActions";

// Words in an answer that point at a page Ask Corral can open for the user.
const AREA_HINTS: Array<[WorkspaceAssistantArea, RegExp]> = [
  ["pipeline", /\b(pipeline|deals?|forecast|stages?)\b/i],
  ["revenue", /\b(revenue|closed[- ]won|won)\b/i],
  ["leads", /\b(leads?|qualified|hot|warm|needs qualifying)\b/i],
  ["review", /\b(manual review|review queue|needs review)\b/i],
  ["email", /\b(inbox|emails?|replies|threads?)\b/i],
  ["calendar", /\b(calendar|meetings?|appointments?)\b/i],
  ["dialer", /\b(calls?|dialer|voicemail)\b/i],
  ["contacts", /\bcontacts?\b/i],
  ["accounts", /\b(compan(?:y|ies)|accounts?)\b/i],
  ["performance", /\b(team performance|reps?|leaderboard)\b/i],
  ["analytics", /\b(analytics|conversion|win rate|response time)\b/i],
  ["agents", /\b(ai activity|agent activity)\b/i],
  ["automations", /\b(sequences?|playbooks?|automations?)\b/i],
  ["integrations", /\b(integrations?|connected|gmail|hubspot|salesforce)\b/i],
  ["icp", /\b(icp|ideal customer)\b/i],
];

function areaHref(area: WorkspaceAssistantArea) {
  return `${window.location.origin}/?view=${area}`;
}

/**
 * Buttons under an answer that take the user to the place the answer is about:
 * open the page, copy a link to it, and copy the figure itself.
 */
export function AssistantResultActions({
  content,
  permissions,
  onNavigate,
}: {
  content: string;
  permissions: ReadonlySet<string>;
  onNavigate: (area: WorkspaceAssistantArea) => void;
}) {
  const [copied, setCopied] = useState<string | null>(null);
  const areas = AREA_HINTS.filter(([area, pattern]) => pattern.test(content) && canOpenAssistantArea(area, permissions)).map(([area]) => area).slice(0, 3);
  const figure = content.match(/(?:[$£€]\s?)?\d[\d,]*(?:\.\d+)?\s?(?:%|k|K|M)?/)?.[0]?.trim();
  if (!areas.length && !figure) return null;

  const copy = async (key: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(key);
      window.setTimeout(() => setCopied((current) => (current === key ? null : current)), 1600);
    } catch {
      setCopied(null);
    }
  };

  return (
    <div className="corral-result-actions" role="group" aria-label="Go to the source">
      {areas.map((area) => (
        <span className="corral-result-pair" key={area}>
          <button type="button" className="corral-result-open" onClick={() => onNavigate(area)}>
            <Icon name="arrowUpRight" size={13} />Open {ASSISTANT_AREAS[area].label}
          </button>
          <button type="button" className="corral-result-copy" aria-label={`Copy link to ${ASSISTANT_AREAS[area].label}`} title="Copy link" onClick={() => void copy(area, areaHref(area))}>
            <Icon name={copied === area ? "check" : "link"} size={13} />
          </button>
        </span>
      ))}
      {figure && (
        <button type="button" className="corral-result-open" onClick={() => void copy("figure", figure)}>
          <Icon name={copied === "figure" ? "check" : "copy"} size={13} />{copied === "figure" ? "Copied" : `Copy ${figure}`}
        </button>
      )}
    </div>
  );
}
