import { useEffect, useRef, useState } from "react";
import { createClientId } from "../clientIds";
import { FeatureGuide } from "../components/FeatureGuide";
import { AutomationEnrollmentDialog } from "../components/AutomationEnrollmentDialog";
import { OperationsDialog } from "../components/OperationsDialog";
import {
  opsGet,
  opsSave,
  type Automation,
  type AutomationSpec,
  type NodeType,
  type PlaybookNode,
  type Run,
} from "../operationsApi";

const nodeTypes: NodeType[] = [
  "wait",
  "condition",
  "draft",
  "research",
  "coach",
  "note",
  "end",
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
type WaitUnit = "hours" | "days" | "weeks";
const waitUnitHours: Record<WaitUnit, number> = {
  hours: 1,
  days: 24,
  weeks: 168,
};
function waitUnitFor(hours: number): WaitUnit {
  if (hours % waitUnitHours.weeks === 0) return "weeks";
  if (hours % waitUnitHours.days === 0) return "days";
  return "hours";
}
function formatWait(hours: number) {
  const unit = waitUnitFor(hours);
  const amount = hours / waitUnitHours[unit];
  const singular = unit.slice(0, -1);
  return `${amount} ${singular}${amount === 1 ? "" : "s"}`;
}
export function AutomationsView({
  canManage,
  canEdit,
  onInspect,
}: {
  canManage: boolean;
  canEdit: boolean;
  onInspect: (id: string) => void;
}) {
  const [definitions, setDefinitions] = useState<Automation[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [offset, setOffset] = useState(0);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [selected, setSelected] = useState("start");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [enrolling, setEnrolling] = useState<Automation | null>(null);
  const [trust, setTrust] = useState<{
    score: number | null;
    sample_size: number;
    method: string;
  } | null>(null);
  const canvas = useRef<HTMLDivElement>(null);
  const drag = useRef<{
    id: string;
    x: number;
    y: number;
    px: number;
    py: number;
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
  function patch(patch: Partial<AutomationSpec>) {
    setEditor((current) =>
      current ? { ...current, spec: { ...current.spec, ...patch } } : null,
    );
  }
  function patchNode(id: string, patch: Partial<PlaybookNode>) {
    setEditor((current) =>
      current
        ? {
            ...current,
            spec: {
              ...current.spec,
              nodes: current.spec.nodes.map((node) =>
                node.id === id ? { ...node, ...patch } : node,
              ),
            },
          }
        : null,
    );
  }
  async function save() {
    if (!editor) return;
    setBusy(true);
    setError("");
    try {
      await opsSave(`/v1/automations/${editor.id}`, editor.spec, "PUT");
      setEditor(null);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to save");
    } finally {
      setBusy(false);
    }
  }
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
  const node = editor?.spec.nodes.find((item) => item.id === selected);
  return (
    <div className="page-view ops-page">
      <header className="ops-page-head">
        <div>
          <span className="eyebrow">Durable steps · human approval</span>
          <h1>Sequences & playbooks</h1>
          <p>
            Branch on recorded signals, schedule draft preparation, and review
            every outgoing email.
          </p>
        </div>
        {canManage && (
          <div className="ops-actions">
            {(["sequence", "playbook"] as const).map((kind) => (
              <button
                className={kind === "sequence" ? "primary-button" : "secondary-button"}
                key={kind}
                onClick={() => {
                  setError("");
                  setSelected("start");
                  setEditor({ id: createClientId(), spec: initial(kind) });
                }}
              >
                New {kind}
              </button>
            ))}
          </div>
        )}
      </header>
      {error && (
        <p className="team-alert" role="alert">
          {error}
        </p>
      )}
      <div className="ops-actions"><FeatureGuide topic="sequence" /><FeatureGuide topic="playbook" /></div>
      {trust && (
        <p className="ops-muted">
          Operational trust:{" "}
          {trust.score === null
            ? "Not enough completed runs"
            : `${trust.score}%`}{" "}
          · {trust.sample_size} observed runs. {trust.method}
        </p>
      )}
      <div className="ops-card-grid">
        {definitions.map((item) => (
          <article className="ops-panel" key={item.id}>
            <span className="ops-tag">
              {item.kind} · {item.active ? "Active" : "Paused"} · v
              {item.version}
            </span>
            <h2>{item.name}</h2>
            <p>
              {item.specification.nodes.length} steps ·{" "}
              {item.specification.timezone}
            </p>
            <div className="ops-actions">
              {canManage && (
                <button className={item.active ? "button-quiet" : "primary-button"} disabled={busy} onClick={async () => {
                  setBusy(true); setError("");
                  try { await opsSave(`/v1/automations/${item.id}`, { ...item.specification, version: item.version, active: !item.active }, "PUT"); await load(); }
                  catch (reason) { setError(reason instanceof Error ? reason.message : "The definition could not be updated."); }
                  finally { setBusy(false); }
                }}>{item.active ? "Pause definition" : "Activate definition"}</button>
              )}
              {canManage && (
                <button
                  className="secondary-button"
                  onClick={() => {
                    setSelected("start");
                    setEditor({
                      id: item.id,
                      spec: { ...item.specification, version: item.version },
                    });
                  }}
                >
                  Edit definition
                </button>
              )}
              {canEdit && (
                <button
                  className="secondary-button"
                  disabled={!item.active}
                  onClick={() => {
                    setEnrolling(item);
                  }}
                >
                  Enroll leads
                </button>
              )}
            </div>
          </article>
        ))}
      </div>
      {!definitions.length && (
        <p className="ops-empty">
          Create a sequence or playbook to begin. Saving an active definition
          does not enroll existing leads.
        </p>
      )}
      <section className="ops-panel">
        <div className="ops-page-head">
          <h2>Run history</h2>
          <button className="secondary-button" onClick={() => void load()}>
            Refresh runs
          </button>
        </div>
        <div className="ops-table-wrap" role="region" aria-label="Table; scroll horizontally to view all columns" tabIndex={0}>
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
              {!runs.length && <tr><td colSpan={4}><div className="ops-empty-inline"><strong>No runs yet</strong><span>Enroll a lead in an active sequence or playbook to see its progress here.</span></div></td></tr>}
              {runs.map((run) => (
                <tr key={run.id}>
                  <td>
                    <button
                      className="text-link"
                      onClick={() => onInspect(run.lead_id)}
                    >
                      {run.lead_id.slice(0, 8)}
                    </button>
                  </td>
                  <td>
                    {run.cursor || "Finished"} ·{" "}
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
                                if (
                                  window.confirm(
                                    "Cancel this run? Its recorded history will be kept.",
                                  )
                                )
                                  void action(run, "cancel");
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
      </section>
      {editor && (
        <OperationsDialog
          title={
            editor.spec.kind === "playbook"
              ? "Visual playbook builder"
              : "Sequence builder"
          }
          onClose={() => setEditor(null)}
        >
          <form
            className="ops-form"
            onSubmit={(event) => {
              event.preventDefault();
              void save();
            }}
          >
            <div className="ops-form-grid">
              <label>
                Name
                <input
                  required
                  maxLength={120}
                  value={editor.spec.name}
                  onChange={(event) => patch({ name: event.target.value })}
                />
              </label>
              <label>
                Timezone
                <input
                  required
                  value={editor.spec.timezone}
                  onChange={(event) => patch({ timezone: event.target.value })}
                  placeholder="Asia/Karachi"
                />
              </label>
              <label>
                Preferred contact-local hour
                <input
                  type="number"
                  min={0}
                  max={23}
                  value={editor.spec.preferred_hour}
                  onChange={(event) =>
                    patch({ preferred_hour: Number(event.target.value) })
                  }
                />
              </label>
              <label className="ops-check">
                <input
                  type="checkbox"
                  checked={editor.spec.active}
                  onChange={(event) => patch({ active: event.target.checked })}
                />
                Active for enrolled leads
              </label>
              <label className="ops-check">
                <input
                  type="checkbox"
                  checked={editor.spec.optimize_time}
                  onChange={(event) =>
                    patch({ optimize_time: event.target.checked })
                  }
                />
                Suggest timing from at least three recorded replies
              </label>
            </div>
            <p className="ops-muted">
              Select a step to configure it. Drag steps to arrange the canvas,
              or use arrow keys on a focused step. Connections are editable
              below. Every branch must reach an end; cycles and disconnected
              steps are rejected.
            </p>
            <div
              className="ops-builder"
              ref={canvas}
              onPointerMove={(event) => {
                if (!drag.current) return;
                patchNode(drag.current.id, {
                  x: Math.max(
                    0,
                    Math.min(
                      1800,
                      drag.current.x + event.clientX - drag.current.px,
                    ),
                  ),
                  y: Math.max(
                    0,
                    Math.min(
                      1100,
                      drag.current.y + event.clientY - drag.current.py,
                    ),
                  ),
                });
              }}
              onPointerUp={() => {
                drag.current = null;
              }}
              onPointerCancel={() => {
                drag.current = null;
              }}
            >
              <div className="ops-canvas">
                <svg width="2050" height="1350" aria-hidden="true">
                  {editor.spec.nodes.flatMap((from) =>
                    (
                      [
                        ["next", from.next],
                        ["otherwise", from.otherwise],
                      ] as const
                    ).map(([branch, target]) => {
                      const to = editor.spec.nodes.find(
                        (item) => item.id === target,
                      );
                      return to ? (
                        <g key={from.id + branch}>
                          <path
                            className={
                              branch === "otherwise" ? "false-branch" : ""
                            }
                            d={`M ${from.x + 170} ${from.y + 35} C ${from.x + 210} ${from.y + 35}, ${to.x - 40} ${to.y + 35}, ${to.x} ${to.y + 35}`}
                          />
                          <text x={from.x + 176} y={from.y + 24}>
                            {from.type === "condition"
                              ? branch === "next"
                                ? "Yes"
                                : "No"
                              : ""}
                          </text>
                        </g>
                      ) : null;
                    }),
                  )}
                </svg>
                {editor.spec.nodes.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    aria-label={`${item.type} step ${item.id}. Use arrow keys to move.`}
                    aria-pressed={selected === item.id}
                    className={`ops-node ${selected === item.id ? "selected" : ""}`}
                    style={{ left: item.x, top: item.y }}
                    onClick={() => setSelected(item.id)}
                    onPointerDown={(event) => {
                      if (event.button !== 0) return;
                      event.currentTarget.setPointerCapture(event.pointerId);
                      drag.current = {
                        id: item.id,
                        x: item.x,
                        y: item.y,
                        px: event.clientX,
                        py: event.clientY,
                      };
                      setSelected(item.id);
                    }}
                    onKeyDown={(event) => {
                      const direction = (
                        {
                          ArrowLeft: [-20, 0],
                          ArrowRight: [20, 0],
                          ArrowUp: [0, -20],
                          ArrowDown: [0, 20],
                        } as Record<string, number[]>
                      )[event.key];
                      if (direction) {
                        event.preventDefault();
                        patchNode(item.id, {
                          x: Math.max(0, Math.min(1800, item.x + direction[0])),
                          y: Math.max(0, Math.min(1100, item.y + direction[1])),
                        });
                      }
                    }}
                  >
                    <b>{item.type}</b>
                    <small>
                      {item.type === "wait"
                        ? formatWait(item.wait_hours)
                        : item.type === "condition"
                          ? item.condition
                          : item.id}
                    </small>
                  </button>
                ))}
              </div>
            </div>
            {node && (
              <section className="ops-panel">
                <h3>Selected step: {node.id}</h3>
                <div className="ops-form-grid">
                  {node.type !== "start" && node.type !== "end" && (
                    <label>
                      Action
                      <select
                        value={node.type}
                        onChange={(event) =>
                          patchNode(node.id, {
                            type: event.target.value as NodeType,
                            next:
                              event.target.value === "end" ? null : node.next,
                            otherwise:
                              event.target.value === "condition"
                                ? node.otherwise
                                : null,
                          })
                        }
                      >
                        {nodeTypes.map((type) => (
                          <option key={type}>{type}</option>
                        ))}
                      </select>
                    </label>
                  )}
                  {node.type !== "end" && (
                    <label>
                      {node.type === "condition" ? "If yes" : "Next step"}
                      <select
                        required
                        value={node.next || ""}
                        onChange={(event) =>
                          patchNode(node.id, {
                            next: event.target.value || null,
                          })
                        }
                      >
                        <option value="">Select step</option>
                        {editor.spec.nodes
                          .filter(
                            (item) =>
                              item.id !== node.id && item.type !== "start",
                          )
                          .map((item) => (
                            <option key={item.id} value={item.id}>
                              {item.id} ({item.type})
                            </option>
                          ))}
                      </select>
                    </label>
                  )}
                  {node.type === "condition" && (
                    <>
                      <label>
                        Recorded signal
                        <select
                          value={node.condition}
                          onChange={(event) =>
                            patchNode(node.id, {
                              condition: event.target.value,
                            })
                          }
                        >
                          {[
                            "hot",
                            "warm",
                            "replied",
                            "silent",
                            "won",
                            "lost",
                            "stalled",
                            "competitor",
                          ].map((value) => (
                            <option key={value}>{value}</option>
                          ))}
                        </select>
                      </label>
                      <label>
                        If no
                        <select
                          required
                          value={node.otherwise || ""}
                          onChange={(event) =>
                            patchNode(node.id, {
                              otherwise: event.target.value || null,
                            })
                          }
                        >
                          <option value="">Select step</option>
                          {editor.spec.nodes
                            .filter(
                              (item) =>
                                item.id !== node.id && item.type !== "start",
                            )
                            .map((item) => (
                              <option key={item.id} value={item.id}>
                                {item.id}
                              </option>
                            ))}
                        </select>
                      </label>
                    </>
                  )}
                  {node.type === "wait" && (
                    <label>
                      Wait before next step
                      <input
                        type="number"
                        min={1}
                        max={Math.floor(2160 / waitUnitHours[waitUnitFor(node.wait_hours)])}
                        value={
                          node.wait_hours /
                          waitUnitHours[waitUnitFor(node.wait_hours)]
                        }
                        onChange={(event) =>
                          patchNode(node.id, {
                            wait_hours: Math.max(
                              1,
                              Math.min(
                                2160,
                                Number(event.target.value) *
                                  waitUnitHours[waitUnitFor(node.wait_hours)],
                              ),
                            ),
                          })
                        }
                      />
                    </label>
                  )}
                  {node.type === "wait" && (
                    <label>
                      Time unit
                      <select
                        value={waitUnitFor(node.wait_hours)}
                        onChange={(event) => {
                          const unit = event.target.value as WaitUnit;
                          const amount = Math.max(
                            1,
                            Math.round(
                              node.wait_hours / waitUnitHours[unit],
                            ),
                          );
                          patchNode(node.id, {
                            wait_hours: Math.min(
                              2160,
                              amount * waitUnitHours[unit],
                            ),
                          });
                        }}
                      >
                        <option value="hours">Hours</option>
                        <option value="days">Days</option>
                        <option value="weeks">Weeks</option>
                      </select>
                    </label>
                  )}
                  {node.type === "wait" && (
                    <p className="ops-muted">
                      Add a wait step before each follow-up draft to set its
                      cadence, such as every 1 week.
                    </p>
                  )}
                  {node.type === "condition" &&
                    node.condition === "stalled" && (
                      <label>
                        Stalled threshold (hours)
                        <input
                          type="number"
                          min={1}
                          max={2160}
                          value={node.wait_hours}
                          onChange={(event) =>
                            patchNode(node.id, {
                              wait_hours: Number(event.target.value),
                            })
                          }
                        />
                      </label>
                    )}
                  {node.type === "note" && (
                    <label>
                      Shared note
                      <textarea
                        required
                        maxLength={2000}
                        value={node.text}
                        onChange={(event) =>
                          patchNode(node.id, { text: event.target.value })
                        }
                      />
                    </label>
                  )}
                  {node.type !== "start" && (
                    <button
                      type="button"
                      className="secondary-button"
                      onClick={() => {
                        patch({
                          nodes: editor.spec.nodes
                            .filter((item) => item.id !== node.id)
                            .map((item) => ({
                              ...item,
                              next:
                                item.next === node.id ? node.next : item.next,
                              otherwise:
                                item.otherwise === node.id
                                  ? node.next
                                  : item.otherwise,
                            })),
                        });
                        setSelected("start");
                      }}
                    >
                      Remove step
                    </button>
                  )}
                </div>
              </section>
            )}
            <div className="ops-actions">
              <button
                type="button"
                className="secondary-button"
                disabled={editor.spec.nodes.length >= 50}
                onClick={() => {
                  const id = "step_" + createClientId().slice(0, 8);
                  const predecessor = selected === "end" ? editor.spec.nodes.find((item) => item.next === "end") : editor.spec.nodes.find((item) => item.id === selected);
                  if (!predecessor) return;
                  patch({
                    nodes: [
                      ...editor.spec.nodes.map((item) => item.id === predecessor.id ? { ...item, next: id } : item),
                      {
                        id,
                        type: "note",
                        x: 60,
                        y: 160 + editor.spec.nodes.length * 25,
                        next: predecessor.next || "end",
                        otherwise: null,
                        wait_hours: 24,
                        condition: "hot",
                        text: "",
                      },
                    ],
                  });
                  setSelected(id);
                }}
              >
                Add step
              </button>
              <button className="primary-button" disabled={busy}>
                Save definition
              </button>
            </div>
            <p className="ops-muted">
              Existing runs retain the definition they enrolled with. Sequences
              pause on a new reply; closed leads stop outreach. No sequence
              sends email automatically.
            </p>
            {error && (
              <p role="alert" className="team-alert">
                {error}
              </p>
            )}
          </form>
        </OperationsDialog>
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
