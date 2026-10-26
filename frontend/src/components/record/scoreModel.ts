import type { Qualification, ScoreLine } from "../../types";

export const titleCase = (value: string) =>
  value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());

const COMPONENT_LABELS: Record<string, string> = {
  icp_fit: "ICP fit",
  intent_urgency: "Intent & urgency",
  engagement_quality: "Engagement",
};
// Used only when an older record has no per-line breakdown to read maxima from.
const LEGACY_COMPONENT_MAX: Record<string, number> = {
  icp_fit: 40,
  intent_urgency: 35,
  engagement_quality: 25,
};

export type ComponentTotal = { key: string; label: string; points: number; max: number; lines: ScoreLine[] };

/** Groups the recorded score lines by component. Reads, never recalculates. */
export function componentTotals(qualification: Pick<Qualification, "score_breakdown" | "component_scores">): ComponentTotal[] {
  const lines = qualification.score_breakdown ?? [];
  if (lines.length) {
    const byComponent = new Map<string, ComponentTotal>();
    for (const line of lines) {
      const entry = byComponent.get(line.component) ?? {
        key: line.component,
        label: COMPONENT_LABELS[line.component] ?? titleCase(line.component),
        points: 0,
        max: 0,
        lines: [],
      };
      entry.points += line.points;
      entry.max += line.max_points;
      entry.lines.push(line);
      byComponent.set(line.component, entry);
    }
    return [...byComponent.values()];
  }
  return Object.entries(qualification.component_scores).map(([key, points]) => ({
    key,
    label: COMPONENT_LABELS[key] ?? titleCase(key),
    points,
    max: LEGACY_COMPONENT_MAX[key] ?? 0,
    lines: [],
  }));
}
