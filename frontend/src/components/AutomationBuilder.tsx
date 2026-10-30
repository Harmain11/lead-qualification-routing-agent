import { useMemo, useState } from "react";
import { createClientId } from "../clientIds";
import { flowProblems, insertStepAfter, orderSteps, removeStep } from "../automationFlow";
import { opsSave, type AutomationSpec, type NodeType, type PlaybookNode } from "../operationsApi";
import { Icon, type IconName } from "../v3/ui/Icon";
import { Banner, Button, IconButton, Sheet } from "../v3/ui/primitives";

const STEP_META: Record<NodeType, { label: string; icon: IconName; hint?: string }> = {
  start: { label: "Lead is enrolled", icon: "flag" },
  wait: { label: "Wait", icon: "timer" },
  draft: { label: "Draft an email", icon: "mail", hint: "What should this email say? Corral drafts it for review; nothing sends on its own." },
  research: { label: "Research", icon: "research", hint: "What should Corral look for before the next touch?" },
  coach: { label: "Coaching tip", icon: "sparkle", hint: "Advice shown to the rep at this point." },
  note: { label: "Note on the lead", icon: "note", hint: "Text saved on the lead for the team." },
  condition: { label: "Check a signal", icon: "route" },
  end: { label: "End", icon: "check" },
};
const ADDABLE: NodeType[] = ["wait", "draft", "research", "coach", "note", "condition"];
const SIGNALS: Array<{ value: string; label: string }> = [
  { value: "hot", label: "Lead is hot" },
  { value: "warm", label: "Lead is warm" },
  { value: "replied", label: "Lead replied" },
  { value: "silent", label: "No reply yet" },
  { value: "won", label: "Deal won" },
  { value: "lost", label: "Deal lost" },
  { value: "stalled", label: "Deal stalled" },
  { value: "competitor", label: "Competitor mentioned" },
];
const UNITS = [
  { value: 1, label: "hours" },
  { value: 24, label: "days" },
  { value: 168, label: "weeks" },
];

function unitFor(hours: number) {
  return hours % 168 === 0 ? 168 : hours % 24 === 0 ? 24 : 1;
}

function hourLabel(hour: number) {
  return new Date(2000, 0, 1, hour).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

const TIMEZONES: string[] = (() => {
  try {
    return (Intl as unknown as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf?.("timeZone") ?? [];
  } catch {
    return [];
  }
})();

export function AutomationBuilder({
  id,
  initial,
  onClose,
  onSaved,
}: {
  id: string;
  initial: AutomationSpec;
  onClose: () => void;
  onSaved: () => void | Promise<void>;
}) {
  const [spec, setSpec] = useState<AutomationSpec>(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [showProblems, setShowProblems] = useState(false);
  const { ordered, unreachable } = useMemo(() => orderSteps(spec.nodes), [spec.nodes]);
  const numbers = useMemo(() => new Map(ordered.concat(unreachable).map((node, index) => [node.id, index])), [ordered, unreachable]);
  const label = (node: PlaybookNode) =>
    node.type === "start" ? "The start" : node.type === "end" ? "End" : `Step ${numbers.get(node.id)} (${STEP_META[node.type].label.toLowerCase()})`;
  const problems = useMemo(() => flowProblems(spec.nodes, label), [spec.nodes, numbers]); // eslint-disable-line react-hooks/exhaustive-deps
  const kindLabel = spec.kind === "playbook" ? "playbook" : "sequence";

  const patchNode = (nodeId: string, patch: Partial<PlaybookNode>) =>
    setSpec((current) => ({ ...current, nodes: current.nodes.map((node) => (node.id === nodeId ? { ...node, ...patch } : node)) }));

  function addAfter(afterId: string, type: NodeType) {
    if (spec.nodes.length >= 50) return;
    const after = spec.nodes.find((node) => node.id === afterId);
    const step: PlaybookNode = {
      id: `step_${createClientId().slice(0, 8)}`, type, x: (after?.x ?? 60), y: Math.min(3900, (after?.y ?? 60) + 80),
      next: null, otherwise: null, wait_hours: type === "wait" ? 72 : 24, condition: "silent", text: "",
    };
    setSpec((current) => {
      const nodes = insertStepAfter(current.nodes, afterId, step);
      if (type !== "condition") return { ...current, nodes };
      const end = nodes.find((node) => node.type === "end")?.id ?? null;
      return { ...current, nodes: nodes.map((node) => (node.id === step.id ? { ...node, otherwise: end } : node)) };
    });
  }

  async function save() {
    setShowProblems(true);
    if (problems.length) return;
    setBusy(true);
    setError("");
    try {
      await opsSave(`/v1/automations/${id}`, spec, "PUT");
      await onSaved();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : `The ${kindLabel} couldn’t be saved.`);
    } finally {
      setBusy(false);
    }
  }

  const targets = (self: PlaybookNode) => spec.nodes.filter((node) => node.id !== self.id && node.type !== "start");

  const renderStep = (node: PlaybookNode, detached = false) => {
    const meta = STEP_META[node.type];
    const editable = node.type !== "start" && node.type !== "end";
    const unit = unitFor(node.wait_hours);
    return (
      <li key={node.id} className="auto-step" data-type={node.type} data-detached={detached || undefined}>
        <span className="auto-step-icon" aria-hidden="true"><Icon name={meta.icon} size={15} /></span>
        <div className="auto-step-body">
          <div className="auto-step-head">
            <span className="auto-step-num">{node.type === "start" || node.type === "end" ? "" : `Step ${numbers.get(node.id)}`}</span>
            {editable ? (
              <label className="auto-step-type">
                <span className="v-sr">Step type</span>
                <select
                  value={node.type}
                  onChange={(event) => {
                    const type = event.target.value as NodeType;
                    const end = spec.nodes.find((item) => item.type === "end")?.id ?? null;
                    patchNode(node.id, { type, otherwise: type === "condition" ? node.otherwise ?? end : null });
                  }}
                >
                  {ADDABLE.map((type) => <option key={type} value={type}>{STEP_META[type].label}</option>)}
                </select>
              </label>
            ) : (
              <b>{meta.label}</b>
            )}
            {editable && (
              <IconButton icon="trash" label={`Remove ${label(node).toLowerCase()}`} size={15} className="auto-step-remove" onClick={() => setSpec((current) => ({ ...current, nodes: removeStep(current.nodes, node.id) }))} />
            )}
          </div>

          {node.type === "wait" && (
            <div className="auto-step-row">
              <label className="v-field auto-inline">
                <span className="v-sr">Wait for</span>
                <input
                  type="number" inputMode="numeric" min={1} max={Math.floor(2160 / unit)}
                  value={node.wait_hours / unit}
                  onChange={(event) => patchNode(node.id, { wait_hours: Math.max(1, Math.min(2160, Math.round(Number(event.target.value) || 1) * unit)) })}
                />
              </label>
              <label className="v-field auto-inline">
                <span className="v-sr">Unit</span>
                <select value={unit} onChange={(event) => patchNode(node.id, { wait_hours: Math.max(1, Math.min(2160, Math.round(node.wait_hours / unit) * Number(event.target.value))) })}>
                  {UNITS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                </select>
              </label>
              <span className="auto-step-help">before the next step</span>
            </div>
          )}

          {(node.type === "draft" || node.type === "research" || node.type === "coach" || node.type === "note") && (
            <label className="v-field">
              <span className="v-sr">{meta.hint}</span>
              <textarea
                rows={2}
                maxLength={2000}
                required={node.type === "note"}
                value={node.text}
                placeholder={meta.hint}
                onChange={(event) => patchNode(node.id, { text: event.target.value })}
              />
            </label>
          )}

          {node.type === "condition" && (
            <div className="auto-step-grid">
              <label className="v-field">
                <span>Check whether</span>
                <select value={node.condition} onChange={(event) => patchNode(node.id, { condition: event.target.value })}>
                  {SIGNALS.map((signal) => <option key={signal.value} value={signal.value}>{signal.label}</option>)}
                </select>
              </label>
              {node.condition === "stalled" && (
                <label className="v-field">
                  <span>Stalled after (days)</span>
                  <input type="number" min={1} max={90} value={Math.max(1, Math.round(node.wait_hours / 24))} onChange={(event) => patchNode(node.id, { wait_hours: Math.max(24, Math.min(2160, Math.round(Number(event.target.value) || 1) * 24)) })} />
                </label>
              )}
              <label className="v-field">
                <span>If yes, go to</span>
                <select value={node.next ?? ""} onChange={(event) => patchNode(node.id, { next: event.target.value || null })}>
                  <option value="">Choose a step</option>
                  {targets(node).map((item) => <option key={item.id} value={item.id}>{label(item)}</option>)}
                </select>
              </label>
              <label className="v-field">
                <span>If no, go to</span>
                <select value={node.otherwise ?? ""} onChange={(event) => patchNode(node.id, { otherwise: event.target.value || null })}>
                  <option value="">Choose a step</option>
                  {targets(node).map((item) => <option key={item.id} value={item.id}>{label(item)}</option>)}
                </select>
              </label>
            </div>
          )}

          {node.type !== "end" && node.type !== "condition" && detached && (
            <label className="v-field">
              <span>Then go to</span>
              <select value={node.next ?? ""} onChange={(event) => patchNode(node.id, { next: event.target.value || null })}>
                <option value="">Choose a step</option>
                {targets(node).map((item) => <option key={item.id} value={item.id}>{label(item)}</option>)}
              </select>
            </label>
          )}

          {node.type !== "end" && node.type !== "condition" && (
            <AddStep disabled={spec.nodes.length >= 50} onAdd={(type) => addAfter(node.id, type)} />
          )}
        </div>
      </li>
    );
  };

  return (
    <Sheet
      title={initial.version ? `Edit ${kindLabel}` : `New ${kindLabel}`}
      subtitle="Steps run top to bottom for each enrolled lead. Drafts always wait for a rep to review and send."
      onClose={onClose}
      width={760}
      footer={
        <>
          <Button kind="gray" onClick={onClose}>Cancel</Button>
          <Button kind="filled" type="submit" form="auto-builder-form" disabled={busy}>{busy ? "Saving…" : "Save"}</Button>
        </>
      }
    >
      <form id="auto-builder-form" className="auto-builder" onSubmit={(event) => { event.preventDefault(); void save(); }} noValidate>
        <div className="auto-builder-settings">
          <label className="v-field auto-name">
            <span>Name</span>
            <input required maxLength={120} value={spec.name} onChange={(event) => setSpec({ ...spec, name: event.target.value })} />
          </label>
          <label className="v-field">
            <span>Time zone</span>
            <input list="auto-builder-zones" required value={spec.timezone} onChange={(event) => setSpec({ ...spec, timezone: event.target.value })} placeholder="Europe/London" />
            <datalist id="auto-builder-zones">{TIMEZONES.map((zone) => <option key={zone} value={zone} />)}</datalist>
          </label>
          <label className="v-field">
            <span>Send drafts around</span>
            <select value={spec.preferred_hour} onChange={(event) => setSpec({ ...spec, preferred_hour: Number(event.target.value) })}>
              {Array.from({ length: 24 }, (_, hour) => <option key={hour} value={hour}>{hourLabel(hour)}</option>)}
            </select>
            <small>In the lead’s local time.</small>
          </label>
          <label className="auto-check">
            <input type="checkbox" checked={spec.optimize_time} onChange={(event) => setSpec({ ...spec, optimize_time: event.target.checked })} />
            <span>Adjust timing once a lead has replied at least three times</span>
          </label>
          <label className="auto-check">
            <input type="checkbox" checked={spec.active} onChange={(event) => setSpec({ ...spec, active: event.target.checked })} />
            <span>Active, so leads can be enrolled</span>
          </label>
        </div>

        <ol className="auto-steps-list" aria-label={`${kindLabel} steps`}>
          {ordered.map((node) => renderStep(node))}
        </ol>
        {unreachable.length > 0 && (
          <section className="auto-detached" aria-label="Steps not connected to the flow">
            <h3>Not connected</h3>
            <p>These steps never run. Point another step at them, or remove them.</p>
            <ol className="auto-steps-list">{unreachable.map((node) => renderStep(node, true))}</ol>
          </section>
        )}

        {showProblems && problems.length > 0 && (
          <Banner tone="danger" title="Fix these before saving">
            <ul className="auto-problems">{problems.map((problem) => <li key={problem}>{problem}</li>)}</ul>
          </Banner>
        )}
        {error && <Banner tone="danger">{error}</Banner>}
        <p className="auto-builder-foot">Leads already enrolled keep the version they started with. A new reply pauses the {kindLabel}; closed leads stop receiving outreach.</p>
      </form>
    </Sheet>
  );
}

function AddStep({ onAdd, disabled }: { onAdd: (type: NodeType) => void; disabled: boolean }) {
  const [open, setOpen] = useState(false);
  if (!open) {
    return (
      <button type="button" className="auto-add" disabled={disabled} onClick={() => setOpen(true)}>
        <Icon name="plus" size={13} /> Add step
      </button>
    );
  }
  return (
    <div className="auto-add-menu" role="group" aria-label="Choose a step to add">
      {ADDABLE.map((type) => (
        <button key={type} type="button" onClick={() => { onAdd(type); setOpen(false); }}>
          <Icon name={STEP_META[type].icon} size={14} />{STEP_META[type].label}
        </button>
      ))}
      <button type="button" className="auto-add-cancel" onClick={() => setOpen(false)}>Cancel</button>
    </div>
  );
}
