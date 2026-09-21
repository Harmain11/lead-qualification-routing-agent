import { useEffect, useState } from "react";
import type { CSSProperties, FormEvent } from "react";
import { createLead, listLeads } from "./api";
import type { ContextFields, Lead, LeadPayload } from "./types";

const emptyForm: LeadPayload = {
  source: "website", source_detail: "manual-test-form", full_name: "", email: "",
  phone: "", job_title: "", company_name: "", industry: "", employee_count: null,
  annual_revenue: null, geography: "", budget: null, product_or_service: "", message: "",
};

const contextOrder: (keyof ContextFields)[] = [
  "requested_product_service", "pain_point", "urgency", "explicit_timeline",
  "budget_information", "company_size_or_spend", "person_role", "decision_maker_status",
  "buying_stage", "competitor_or_current_tools", "technical_requirements",
  "previous_engagement", "source_channel_intent",
];

function label(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function Lifecycle({ lead }: { lead: Lead | null }) {
  const stages = ["Intake", "Normalize", "Analyze", "Qualify", "Route", "Notify"];
  const completed = lead ? (lead.routing ? 6 : lead.qualification ? 4 : 2) : 0;
  return <div className="lifecycle" aria-label="Lead lifecycle">
    {stages.map((stage, index) => <div className={`lifecycle-node ${index < completed ? "completed" : index === completed ? "active" : ""}`} key={stage}>
      <span>{String(index + 1).padStart(2, "0")}</span><b>{stage}</b>{index < stages.length - 1 && <i />}
    </div>)}
  </div>;
}

function AnalysisPanel({ lead }: { lead: Lead }) {
  const analysis = lead.analysis;
  if (!analysis) return <p className="muted">Context analysis has not been recorded.</p>;
  const icpCriteria = [
    ["Industry", analysis.icp_evaluation.industry_fit],
    ["Company size", analysis.icp_evaluation.company_size_fit],
    ["Geography", analysis.icp_evaluation.geography_fit],
    ["Buyer role", analysis.icp_evaluation.buyer_role_fit],
    ["Budget", analysis.icp_evaluation.budget_fit],
    ["Pain relevance", analysis.icp_evaluation.pain_point_relevance],
  ] as const;

  return (
    <section className="analysis-stack">
      <div className="analysis-header surface">
        <div><span className="eyebrow">AI context layer</span><h3>Signal extraction</h3><p>{analysis.provider} · {analysis.model} · {analysis.prompt_version}</p></div>
        <div className="confidence-orbit" style={{ "--confidence": `${analysis.overall_confidence * 360}deg` } as CSSProperties}><strong>{Math.round(analysis.overall_confidence * 100)}</strong><span>% confidence</span></div>
      </div>
      {analysis.requires_manual_review && <div className="review-alert"><span>Review required</span>{analysis.error_message ?? "Confidence policy requested human review."}</div>}
      <div className="summary-card surface">
        <span className="eyebrow">Sales brief</span><h3>{analysis.summary.who}</h3>
        <p className="summary-lead">{analysis.summary.company_context} — {analysis.summary.need}</p>
        <div className="summary-grid"><Data label="Pain" value={analysis.summary.pain_point} /><Data label="Urgency" value={analysis.summary.urgency} /><Data label="Buying signal" value={analysis.summary.buying_signal} /><Data label="Potential concern" value={analysis.summary.potential_concern} /></div>
        <div className="action-note"><span>Prepared action</span>{analysis.recommended_action}</div>
      </div>
      <div className="surface signal-surface">
        <div className="section-title"><div><span className="eyebrow">Extracted context</span><h3>Evidence map</h3></div><span className={`intent intent-${analysis.intent_level}`}>{analysis.intent_level} intent</span></div>
        <div className="signal-grid">{contextOrder.map((key) => { const signal = analysis.context[key]; return <article className={`signal-card ${signal.information_kind}`} key={key}><div><span>{label(key)}</span><i>{signal.information_kind}</i></div><strong>{signal.value ?? "Unknown"}</strong>{signal.evidence[0] && <small>{signal.evidence[0].source_field}: “{signal.evidence[0].excerpt}”</small>}</article>; })}</div>
      </div>
      <div className="surface icp-surface">
        <div className="section-title"><div><span className="eyebrow">Grounded evaluation</span><h3>{analysis.icp_evaluation.profile_name}</h3></div><code>{analysis.icp_evaluation.profile_version}</code></div>
        <div className="icp-grid">{icpCriteria.map(([name, criterion]) => <div className="icp-row" key={name}><span className={`fit-dot ${criterion.status}`} /><div><b>{name}</b><small>{criterion.rationale}</small></div><em>{label(criterion.status)}</em></div>)}</div>
      </div>
    </section>
  );
}

function DecisionPanel({ lead }: { lead: Lead }) {
  const qualification = lead.qualification;
  if (!qualification) return <p className="muted">No qualification result yet.</p>;
  const routing = lead.routing;
  return <section className="decision-grid">
    <div className="score-card surface"><span className="eyebrow">Deterministic score</span><strong>{qualification.total_score}<small>/100</small></strong><span className={`pill ${qualification.classification}`}>{label(qualification.classification)}</span></div>
    <div className="route-card surface"><span className="eyebrow">Routing decision</span><strong>{routing ? label(routing.assigned_queue) : label(qualification.routing_queue)}</strong><span>{routing?.assigned_rep_name ? `Assigned to ${routing.assigned_rep_name}` : routing?.manual_review ? "Manual review required" : "No rep assigned"}</span><small>{routing ? `${label(routing.priority)} priority · ${routing.notification_state} notification` : "Routing pending"}</small></div>
    <div className="breakdown-card surface"><span className="eyebrow">40 / 35 / 25 contract</span><div className="component-totals">{Object.entries(qualification.component_scores).map(([component, points]) => { const maximum = component === "icp_fit" ? 40 : component === "intent_urgency" ? 35 : 25; return <div key={component}><span>{label(component)}</span><b>{points}<small>/{maximum}</small></b><i style={{ width: `${points / maximum * 100}%` }} /></div>; })}</div><div className="score-lines">{qualification.score_breakdown.map((line) => <div className="score-line" key={`${line.component}-${line.key}`}><div><b>{label(line.key)}</b><small>{line.reason}</small>{line.evidence.length > 0 && <em>{line.evidence.join(" · ")}</em>}</div><strong>{line.points}/{line.max_points}</strong></div>)}</div></div>
    <div className="rationale-card surface"><span className="eyebrow">Business-facing rationale</span>{qualification.rationale.map((reason) => <p key={reason}>{reason}</p>)}</div>
    {routing && <div className="action-plan-card surface"><div className="section-title"><div><span className="eyebrow">Recommended action</span><h3>{routing.action_plan.immediate_action}</h3></div><span className={`notify-state ${routing.notification_state}`}>{routing.notification_state}</span></div><div className="action-plan-grid"><Data label="Response priority" value={routing.action_plan.response_priority} /><Data label="Urgency / timeline" value={routing.action_plan.urgency_timeline} /><Data label="Next step" value={routing.action_plan.next_step_recommendation} /><Data label="Known pain" value={routing.action_plan.known_pain_point} /></div><div className="action-columns"><div><span className="eyebrow">Talking points</span>{routing.action_plan.talking_points.map((point) => <p key={point}>{point}</p>)}</div><div><span className="eyebrow">Open questions</span>{routing.action_plan.unanswered_questions.length ? routing.action_plan.unanswered_questions.map((question) => <p key={question}>{question}</p>) : <p>No unanswered qualification questions.</p>}</div></div><div className="route-explain"><span className="eyebrow">Routing rationale</span><p>{routing.routing_reason}</p><small>Rules: {routing.matched_rules.join(" · ")}</small></div></div>}
  </section>;
}

function App() {
  const [form, setForm] = useState<LeadPayload>(emptyForm);
  const [leads, setLeads] = useState<Lead[]>([]);
  const [selected, setSelected] = useState<Lead | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  useEffect(() => { listLeads().then(setLeads).catch(() => undefined); }, []);
  function update<K extends keyof LeadPayload>(key: K, value: LeadPayload[K]) { setForm((current) => ({ ...current, [key]: value })); }
  async function submit(event: FormEvent) {
    event.preventDefault(); setSubmitting(true); setError(null);
    try { const created = await createLead({ ...form }); setLeads((current) => [created, ...current.filter((lead) => lead.id !== created.id)]); setSelected(created); setForm(emptyForm); }
    catch (submissionError) { setError(submissionError instanceof Error ? submissionError.message : "Unable to submit lead"); }
    finally { setSubmitting(false); }
  }

  return <main className="shell">
    <div className="ambient ambient-one" /><div className="ambient ambient-two" />
    <header className="topbar"><div className="brand"><span className="brand-mark"><i /><i /></span><div><b>INGENUITY SYSTEMS</b><small>LEAD INTELLIGENCE</small></div></div><div className="status-badge"><span /> PHASE 4 · ROUTING ONLINE</div></header>
    <section className="hero"><div><span className="eyebrow">Context → evidence → decision → action</span><h1>Every signal,<br /><em>made actionable.</em></h1><p>A grounded AI reasoning layer interprets the inquiry. Deterministic policy keeps every score bounded while routing and action plans make the next move clear.</p></div><Lifecycle lead={selected} /></section>
    <section className="workspace">
      <form className="lead-form panel" onSubmit={submit}><div className="panel-heading"><div><span className="eyebrow">01 / Intake console</span><h2>Submit a test lead</h2></div><span className="live-dot">Live</span></div><div className="form-grid">
        <Field label="Full name" value={form.full_name} onChange={(value) => update("full_name", value)} required /><Field label="Email" type="email" value={form.email} onChange={(value) => update("email", value)} required /><Field label="Company" value={form.company_name} onChange={(value) => update("company_name", value)} required /><Field label="Job title" value={form.job_title ?? ""} onChange={(value) => update("job_title", value)} /><Field label="Industry" value={form.industry ?? ""} onChange={(value) => update("industry", value)} /><Field label="Geography" value={form.geography ?? ""} onChange={(value) => update("geography", value)} /><Field label="Employees" type="number" value={form.employee_count ?? ""} onChange={(value) => update("employee_count", value ? Number(value) : null)} /><Field label="Budget (USD)" type="number" value={form.budget ?? ""} onChange={(value) => update("budget", value ? Number(value) : null)} /><Field label="Product or service" value={form.product_or_service} onChange={(value) => update("product_or_service", value)} required wide />
      </div><label className="field full-width"><span>Inquiry</span><textarea value={form.message} onChange={(event) => update("message", event.target.value)} placeholder="Describe the pain, timeline, budget, current tools, and desired outcome…" required rows={7} /></label>{error && <p className="error">{error}</p>}<button disabled={submitting} type="submit"><span>{submitting ? "Analyzing…" : "Run intelligence pipeline"}</span><i>→</i></button></form>
      <section className="results panel"><div className="panel-heading"><div><span className="eyebrow">02 / Inspection bay</span><h2>{selected ? selected.company_name : "Awaiting lead"}</h2></div>{selected && <span className="source-chip">{selected.source}</span>}</div>{selected ? <><div className="normalized surface"><div className="section-title"><div><span className="eyebrow">Canonical record</span><h3>Normalized lead</h3></div><code>v{selected.normalization_version}</code></div><div className="normalized-grid"><Data label="Name" value={selected.full_name} /><Data label="Company" value={selected.company_name} /><Data label="Source" value={`${selected.source} · ${selected.source_detail ?? "unspecified"}`} /><Data label="Contact" value={selected.email} /><Data label="Product" value={selected.product_or_service} /><Data label="Received" value={new Date(selected.received_at).toLocaleString()} /></div><p className="message-preview">“{selected.message}”</p></div><AnalysisPanel lead={selected} /><DecisionPanel lead={selected} /></> : <div className="empty-state"><span className="empty-orbit"><i /><i /><i /></span><b>Pipeline standing by</b><p>Submit a lead or select a record to inspect normalization, context, evidence, ICP grounding, qualification, routing, and action plan.</p></div>}</section>
    </section>
    <section className="lead-list panel"><div className="panel-heading"><div><span className="eyebrow">03 / Evidence archive</span><h2>Recent leads</h2></div><span className="count">{leads.length.toString().padStart(2, "0")} records</span></div><div className="records">{leads.length === 0 ? <p className="muted">No persisted leads yet.</p> : leads.map((lead) => <button className={`record ${selected?.id === lead.id ? "active" : ""}`} key={lead.id} onClick={() => setSelected(lead)}><span className="record-source">{lead.source.slice(0, 2).toUpperCase()}</span><span><b>{lead.full_name}</b><small>{lead.company_name} · {lead.analysis?.intent_level ?? "unanalyzed"} intent</small></span><span className={`pill ${lead.qualification?.classification ?? ""}`}>{lead.qualification ? `${lead.qualification.total_score} · ${label(lead.qualification.classification)}` : "Pending"}</span></button>)}</div></section>
  </main>;
}

function Field({ label: fieldLabel, value, onChange, type = "text", required = false, wide = false }: { label: string; value: string | number; onChange: (value: string) => void; type?: string; required?: boolean; wide?: boolean }) { return <label className={`field ${wide ? "wide" : ""}`}><span>{fieldLabel}</span><input type={type} value={value} onChange={(event) => onChange(event.target.value)} required={required} /></label>; }
function Data({ label: dataLabel, value }: { label: string; value: string }) { return <div><span>{dataLabel}</span><b>{value || "Not provided"}</b></div>; }
export default App;
