import type { PlaybookNode } from "./operationsApi";

/**
 * Pure helpers for editing a sequence or playbook as an ordered list of steps.
 * The stored graph (next / otherwise links) stays the source of truth.
 */

/** Steps in the order they run: depth-first from start, yes-branch first. Unlinked steps last. */
export function orderSteps(nodes: PlaybookNode[]): { ordered: PlaybookNode[]; unreachable: PlaybookNode[] } {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const seen = new Set<string>();
  const ordered: PlaybookNode[] = [];
  const visit = (id: string | null) => {
    if (!id || seen.has(id)) return;
    const node = byId.get(id);
    if (!node) return;
    seen.add(id);
    ordered.push(node);
    visit(node.next);
    if (node.type === "condition") visit(node.otherwise);
  };
  visit(nodes.find((node) => node.type === "start")?.id ?? null);
  return { ordered, unreachable: nodes.filter((node) => !seen.has(node.id)) };
}

/** Problems a person must fix before saving, in plain language. Mirrors the server's checks. */
export function flowProblems(nodes: PlaybookNode[], label: (node: PlaybookNode) => string): string[] {
  const problems: string[] = [];
  const ids = new Set(nodes.map((node) => node.id));
  if (nodes.filter((node) => node.type === "start").length !== 1) problems.push("The flow needs exactly one start.");
  if (!nodes.some((node) => node.type === "end")) problems.push("Add an end step.");
  for (const node of nodes) {
    if (node.type !== "end" && (!node.next || !ids.has(node.next))) problems.push(`${label(node)} needs a next step.`);
    if (node.type === "condition" && (!node.otherwise || !ids.has(node.otherwise))) problems.push(`${label(node)} needs a step for “no”.`);
    if (node.type === "note" && !node.text.trim()) problems.push(`${label(node)} needs its note text.`);
  }
  const { unreachable } = orderSteps(nodes);
  if (unreachable.length) problems.push(`${unreachable.length === 1 ? "One step is" : `${unreachable.length} steps are`} not connected to the flow.`);
  if (hasCycle(nodes)) problems.push("The flow loops back on itself. Point a step forward or to an end.");
  return problems;
}

function hasCycle(nodes: PlaybookNode[]): boolean {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const state = new Map<string, 1 | 2>();
  const visit = (id: string | null): boolean => {
    if (!id || !byId.has(id)) return false;
    if (state.get(id) === 1) return true;
    if (state.get(id) === 2) return false;
    state.set(id, 1);
    const node = byId.get(id)!;
    const looped = visit(node.next) || (node.type === "condition" && visit(node.otherwise));
    state.set(id, 2);
    return looped;
  };
  return nodes.some((node) => visit(node.id));
}

/** Inserts a step after `afterId`, taking over its next link. */
export function insertStepAfter(nodes: PlaybookNode[], afterId: string, step: PlaybookNode): PlaybookNode[] {
  const after = nodes.find((node) => node.id === afterId);
  if (!after || after.type === "end") return nodes;
  return [
    ...nodes.map((node) => (node.id === afterId ? { ...node, next: step.id } : node)),
    { ...step, next: after.next ?? nodes.find((node) => node.type === "end")?.id ?? null },
  ];
}

/** Removes a step and reconnects everything that pointed at it to the step it led to. */
export function removeStep(nodes: PlaybookNode[], id: string): PlaybookNode[] {
  const removed = nodes.find((node) => node.id === id);
  if (!removed || removed.type === "start") return nodes;
  const fallback = removed.next ?? nodes.find((node) => node.type === "end" && node.id !== id)?.id ?? null;
  return nodes
    .filter((node) => node.id !== id)
    .map((node) => ({
      ...node,
      next: node.next === id ? fallback : node.next,
      otherwise: node.otherwise === id ? fallback : node.otherwise,
    }));
}
