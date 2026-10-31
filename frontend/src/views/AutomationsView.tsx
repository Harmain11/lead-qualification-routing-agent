import { useEffect, useState } from "react";
import { createClientId } from "../clientIds";
import { FeatureGuide } from "../components/FeatureGuide";
import { AutomationEnrollmentDialog } from "../components/AutomationEnrollmentDialog";
import { AutomationBuilder } from "../components/AutomationBuilder";
import {
  opsGet,
  opsSave,
  type Automation,
  type AutomationSpec,
  type NodeType,
  type Run,
} from "../operationsApi";
import { confirmAction } from "../v3/ui/confirm";
import { Badge, Banner, Button, EmptyState, IconButton, PageHeader } from "../v3/ui/primitives";
import { automationDraftSpec } from "../components/workspaceAssistantActions";

const NODE_LABELS: Record<NodeType, string> = {
  start: "Start", wait: "Wait", condition: "Check", draft: "Draft email",
  research: "Research", coach: "Coaching", note: "Note", end: "End",
};

// Starting points offered when a workspace has no sequences yet.
const AUTOMATION_TEMPLATES = [
  {
    name: "Speed to lead", kind: "sequence", summary: "Same-day reply, then two nudges over a week.",
    steps: [
      { type: "draft", text: "Thank them for reaching out, answer the question they asked, and offer two meeting times." },
      { type: "wait", wait_hours: 48 },
      { type: "draft", text: "Short nudge: one line on the outcome they mentioned and a booking link." },
      { type: "wait", wait_hours: 120 },
      { type: "draft", text: "Polite close-the-loop email that makes it easy to reply later." },
    ],
  },
  {
    name: "Post-demo follow-up", kind: "sequence", summary: "Recap, resources, then a decision check-in.",
    steps: [
      { type: "draft", text: "Recap the demo: their goals, what they saw, agreed next step and owner." },
      { type: "wait", wait_hours: 72 },
      { type: "coach", text: "Call the champion if there is no reply. Ask who else needs to see it." },
      { type: "wait", wait_hours: 96 },
      { type: "draft", text: "Check in on the decision timeline and offer a call with the decision maker." },
    ],
  },
  {
    name: "Re-engage a quiet lead", kind: "sequence", summary: "Fresh angle after silence, with research first.",
    steps: [
      { type: "research", text: "Look for news, hiring or funding at the company since the last touch." },
      { type: "draft", text: "Lead with what changed for them, one relevant result, and a soft question." },
      { type: "wait", wait_hours: 168 },
      { type: "draft", text: "Final note asking whether to close the file or reconnect next quarter." },
    ],
  },
  {
    name: "Qualification handoff", kind: "playbook", summary: "Checklist before a lead goes to an account executive.",
    steps: [
      { type: "note", text: "Confirm fit, need, decision maker, budget range and timeline." },
      { type: "coach", text: "If any are unknown, book a 15-minute discovery call before handoff." },
      { type: "note", text: "Write a three-line handoff summary on the lead for the account executive." },
    ],
  },
];

const initial = (kind: "sequence" | "playbook"): AutomationSpec => ({
  name: kind === "sequence" ? "Follow-up sequence" : "New playbook",
  kind,
  active: false,
  version: 0,
  timezone: "UTC",
  preferred_hour: 9,
  optimize_time: true,
  nodes: [
    {
      id: "start",
      type: "start",
      x: 40,
      y: 60,
      next: kind === "playbook" ? "note" : "wait",
      otherwise: null,
      wait_hours: 24,
      condition: "hot",
      text: "",
    },
    {
      id: kind === "playbook" ? "note" : "wait",
      type: kind === "playbook" ? "note" : "wait",
      x: 270,
      y: 60,
      next: kind === "playbook" ? "end" : "draft",
      otherwise: null,
      wait_hours: 24,
      condition: "hot",
      text: kind === "playbook" ? "Qualification handoff: review the lead’s fit, need, decision maker, timeline and next action. Record verified details before outreach." : "",
    },
    ...(kind === "sequence" ? [{
      id: "draft",
      type: "draft" as const,
      x: 500,
      y: 60,
      next: "end",
      otherwise: null,
      wait_hours: 24,
      condition: "hot",
      text: "",
    }] : []),
    {
      id: "end",
      type: "end",
      x: 730,
      y: 60,
      next: null,
      otherwise: null,
      wait_hours: 24,
      condition: "hot",
      text: "",
    },
  ],
});
type Editor = { id: string; spec: AutomationSpec };
export function AutomationsView({
  canManage,
  canEdit,
  onInspect,
  leadName,
}: {
  canManage: boolean;
  canEdit: boolean;
  onInspect: (id: string) => void;
  leadName?: (id: string) => string | undefined;
}) {
  const [definitions, setDefinitions] = useState<Automation[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [offset, setOffset] = useState(0);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [enrolling, setEnrolling] = useState<Automation | null>(null);
  const [trust, setTrust] = useState<{
    score: number | null;
    sample_size: number;
    method: string;
  } | null>(null);
  function load() {
    return Promise.all([
      opsGet<{ items: Automation[] }>("/v1/automations").then((data) =>
        setDefinitions(data.items),
      ),
      opsGet<{ items: Run[] }>(
        `/v1/automations/runs/list?offset=${offset}`,
      ).then((data) => setRuns(data.items)),
      opsGet<typeof trust>("/v1/automations/trust/summary").then(setTrust),
    ]).catch((reason) => setError(reason.message));
  }
  useEffect(() => {
    const controller = new AbortController();
    opsGet<{ items: Automation[] }>("/v1/automations", controller.signal)
      .then((data) => setDefinitions(data.items))
      .catch((reason) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    opsGet<{ items: Run[] }>(
      `/v1/automations/runs/list?offset=${offset}`,
      controller.signal,
    )
      .then((data) => setRuns(data.items))
      .catch((reason) => { if (!controller.signal.aborted) setError(reason.message); });
    opsGet<typeof trust>("/v1/automations/trust/summary", controller.signal)
      .then(setTrust)
      .catch((reason) => { if (!controller.signal.aborted) setError(reason.message); });
    return () => controller.abort();
  }, [offset]);
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible" && !editor) void load();
    }, 15000);
    return () => window.clearInterval(timer);
  }, [offset, editor]);
  async function action(run: Run, action: string) {
    setBusy(true);
    setError("");
    try {
      await opsSave(`/v1/automations/runs/${run.id}`, { action });
      await load();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Unable to update run",
      );
    } finally {
      setBusy(false);
    }
  }
  function startNew(kind: "sequence" | "playbook") {
    setError("");
    setEditor({ id: createClientId(), spec: initial(kind) });
  }
  async function toggleActive(item: Automation) {
    setBusy(true);
    setError("");
    try {
      await opsSave(`/v1/automations/${item.id}`, { ...item.specification, version: item.version, active: !item.active }, "PUT");
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The sequence could not be updated.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="page-view ops-page auto-page">
      <PageHeader
        title="Sequences & playbooks"
        subtitle="Timed follow-ups and guided plays. Every outgoing email waits for your approval."
        actions={canManage ? (
          <>
            <Button kind="gray" onClick={() => startNew("playbook")}>New playbook</Button>
            <Button kind="filled" icon="plus" onClick={() => startNew("sequence")}>New sequence</Button>
          </>
        ) : undefined}
      />
      {error && <Banner tone="danger">{error}</Banner>}
      {definitions.length > 0 && (
        <div className="auto-grid">
          {definitions.map((item) => {
            const steps = item.specification.nodes.filter((node) => node.type !== "start" && node.type !== "end");
            return (
              <article className="auto-card" key={item.id}>
                <div className="auto-card-head">
                  <Badge tone={item.active ? "tint" : "neutral"} dot>{item.active ? "Active" : "Paused"}</Badge>
                  <span className="auto-kind">{item.kind === "sequence" ? "Sequence" : "Playbook"}</span>
                </div>
                <h2>{item.name}</h2>
                <p className="auto-steps">{steps.length} step{steps.length === 1 ? "" : "s"}</p>
                {steps.length > 0 && (
                  <ol className="auto-chips" aria-label="Steps">
                    {steps.slice(0, 6).map((node, index) => <li key={`${node.id}-${index}`}>{NODE_LABELS[node.type]}</li>)}
                    {steps.length > 6 && <li>+{steps.length - 6}</li>}
                  </ol>
                )}
                <div className="auto-card-actions">
                  {canManage && (
                    <Button
                      size="sm"
                      kind={item.active ? "gray" : "tinted"}
                      disabled={busy}
                      onClick={() => void toggleActive(item)}
                    >
                      {item.active ? "Pause" : "Activate"}
                    </Button>
                  )}
                  {canManage && (
                    <Button
                      size="sm"
                      kind="gray"
                      onClick={() => {
                        setEditor({ id: item.id, spec: { ...item.specification, version: item.version } });
                      }}
                    >
                      Edit
                    </Button>
                  )}
                  {canEdit && (
                    <Button
                      size="sm"
                      kind="gray"
                      disabled={!item.active}
                      title={item.active ? undefined : "Activate it first"}
                      onClick={() => setEnrolling(item)}
                    >
                      Enroll leads
                    </Button>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      )}
      {!definitions.length && (
        <section className="auto-templates" aria-labelledby="auto-templates-title">
          <div>
            <h2 id="auto-templates-title">Start from a template</h2>
            <p>Each one saves as a paused draft you can edit before anyone is enrolled.</p>
          </div>
          <div className="auto-grid">
            {AUTOMATION_TEMPLATES.map((template) => (
              <article className="auto-card" key={template.name}>
                <div className="auto-card-head"><span className="auto-kind">{template.kind === "sequence" ? "Sequence" : "Playbook"}</span></div>
                <h2>{template.name}</h2>
                <p className="auto-steps">{template.summary}</p>
                <ol className="auto-chips" aria-label="Steps">
                  {template.steps.map((step, index) => <li key={index}>{NODE_LABELS[step.type as NodeType]}</li>)}
                </ol>
                {canManage && (
                  <div className="auto-card-actions">
                    <Button size="sm" kind="tinted" disabled={busy} onClick={() => {
                      setError("");
                      setEditor({ id: createClientId(), spec: automationDraftSpec(template) });
                    }}>Use template</Button>
                  </div>
                )}
              </article>
            ))}
          </div>
        </section>
      )}
      <div className="auto-guides">
        <FeatureGuide topic="sequence" />
        <FeatureGuide topic="playbook" />
        {trust && trust.score !== null && trust.sample_size >= 5 && (
          <span className="auto-trust" title={trust.method}>
            {trust.score}% of {trust.sample_size} recent runs completed without being blocked
          </span>
        )}
      </div>
      <section className="auto-runs" aria-labelledby="auto-runs-title">
        <div className="auto-runs-head">
          <h2 id="auto-runs-title">Run history</h2>
          <IconButton icon="refresh" label="Refresh runs" onClick={() => void load()} />
        </div>
        {!runs.length && !offset ? (
          <EmptyState icon="automations" title="No runs yet">
            Enroll a lead in an active sequence or playbook to follow its progress here.
          </EmptyState>
        ) : (
        <>
        <div className="ops-table-wrap" role="region" aria-label="Run history; scroll sideways to see every column" tabIndex={0}>
          <table>
            <thead>
              <tr>
                <th>Lead</th>
                <th>Step / status</th>
                <th>Next draft window</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {!runs.length && <tr><td colSpan={4}><div className="ops-empty-inline"><strong>No more runs</strong><span>Go back to the previous page.</span></div></td></tr>}
              {runs.map((run) => (
                <tr key={run.id}>
                  <td>
                    <button
                      className="text-link"
                      onClick={() => onInspect(run.lead_id)}
                    >
                      {leadName?.(run.lead_id) || "Open lead"}
                    </button>
                  </td>
                  <td>
                    {run.cursor ? `Step ${run.cursor}` : "Finished"} ·{" "}
                    {run.status.replaceAll("_", " ")}
                    {run.last_error && <p role="status">{run.last_error}</p>}
                  </td>
                  <td>
                    {new Date(run.due_at).toLocaleString()}
                    <small>
                      {run.state.timing?.method} {run.state.timing?.timezone}
                    </small>
                  </td>
                  <td>
                    <div className="ops-actions">
                      {canEdit &&
                        !["completed", "cancelled"].includes(run.status) && (
                          <>
                            {run.status === "awaiting_approval" && (
                              <button
                                className="secondary-button"
                                disabled={busy}
                                onClick={() => void action(run, "approve")}
                              >
                                Approve step
                              </button>
                            )}
                            {["paused", "blocked"].includes(run.status) ? (
                              <button
                                className="secondary-button"
                                disabled={busy}
                                onClick={() => void action(run, "resume")}
                              >
                                Review & resume
                              </button>
                            ) : (
                              <button
                                className="secondary-button"
                                disabled={busy || run.status === "processing"}
                                onClick={() => void action(run, "pause")}
                              >
                                Pause
                              </button>
                            )}
                            {["queued", "approved"].includes(run.status) && (
                              <button
                                className="secondary-button"
                                disabled={busy}
                                onClick={() => void action(run, "advance")}
                              >
                                Check due step
                              </button>
                            )}
                            {run.status === "awaiting_draft" && (
                              <button
                                className="secondary-button"
                                onClick={() => onInspect(run.lead_id)}
                              >
                                Review draft
                              </button>
                            )}
                            <button
                              className="secondary-button"
                              disabled={busy || run.status === "processing"}
                              onClick={() => {
                                void confirmAction({ title: "Cancel this run?", message: "No further steps run for this lead. Its recorded history is kept.", confirmLabel: "Cancel run", cancelLabel: "Keep running", tone: "danger" })
                                  .then((confirmed) => { if (confirmed) void action(run, "cancel"); });
                              }}
                            >
                              Cancel
                            </button>
                          </>
                        )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="ops-pagination">
          <span>{runs.length ? `${offset + 1}–${offset + runs.length}` : "0"} runs shown</span>
          <div className="ops-actions">
          <button className="button-quiet"
            disabled={!offset}
            onClick={() => setOffset(Math.max(0, offset - 50))}
          >
            Previous
          </button>
          <button className="button-quiet"
            disabled={runs.length < 50}
            onClick={() => setOffset(offset + 50)}
          >
            Next
          </button>
          </div>
        </div>
        </>
        )}
      </section>
      {editor && (
        <AutomationBuilder
          key={editor.id}
          id={editor.id}
          initial={editor.spec}
          onClose={() => setEditor(null)}
          onSaved={async () => {
            setEditor(null);
            await load();
          }}
        />
      )}
      {enrolling && (
        <AutomationEnrollmentDialog
          kind={enrolling.kind}
          definition={enrolling}
          onClose={() => setEnrolling(null)}
          onEnrolled={() => void load()}
        />
      )}
    </div>
  );
}
