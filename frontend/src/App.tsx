import { FormEvent, useEffect, useState } from "react";
import { createLead, listLeads } from "./api";
import type { Lead, LeadPayload, Qualification } from "./types";

const emptyForm: LeadPayload = {
  source: "website",
  source_detail: "manual-test-form",
  full_name: "",
  email: "",
  phone: "",
  job_title: "",
  company_name: "",
  industry: "",
  employee_count: null,
  annual_revenue: null,
  geography: "",
  budget: null,
  product_or_service: "",
  message: "",
};

function label(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function DecisionPanel({ lead }: { lead: Lead }) {
  const qualification = lead.qualification;
  if (!qualification) return <p className="muted">No qualification result yet.</p>;
  return (
    <div className="decision-grid">
      <div className="score-card">
        <span className="eyebrow">Total score</span>
        <strong>{qualification.total_score}</strong>
        <span className={`pill ${qualification.classification}`}>{label(qualification.classification)}</span>
      </div>
      <div className="route-card">
        <span className="eyebrow">Routing decision</span>
        <strong>{label(qualification.routing_queue)}</strong>
        <span>{label(qualification.routing_action)}</span>
      </div>
      <div className="breakdown-card">
        <span className="eyebrow">Score breakdown</span>
        <div className="component-totals">
          {Object.entries(qualification.component_scores).map(([component, points]) => (
            <div key={component}><span>{label(component)}</span><b>{points}/{component === "icp_fit" ? 40 : component === "intent_urgency" ? 35 : 25}</b></div>
          ))}
        </div>
        {qualification.score_breakdown.map((line) => (
          <div className="score-line" key={`${line.component}-${line.key}`}>
            <div>
              <b>{label(line.key)}</b>
              <small>{line.reason}</small>
            </div>
            <strong>{line.points}/{line.max_points}</strong>
          </div>
        ))}
      </div>
      <div className="rationale-card">
        <span className="eyebrow">Explainable rationale</span>
        {qualification.rationale.map((reason) => <p key={reason}>{reason}</p>)}
      </div>
    </div>
  );
}

function App() {
  const [form, setForm] = useState<LeadPayload>(emptyForm);
  const [leads, setLeads] = useState<Lead[]>([]);
  const [selected, setSelected] = useState<Lead | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    listLeads().then(setLeads).catch(() => undefined);
  }, []);

  function update<K extends keyof LeadPayload>(key: K, value: LeadPayload[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const created = await createLead({
        ...form,
        employee_count: form.employee_count ? Number(form.employee_count) : null,
        annual_revenue: form.annual_revenue ? Number(form.annual_revenue) : null,
        budget: form.budget ? Number(form.budget) : null,
      });
      setLeads((current) => [created, ...current.filter((lead) => lead.id !== created.id)]);
      setSelected(created);
      setForm(emptyForm);
    } catch (submissionError) {
      setError(submissionError instanceof Error ? submissionError.message : "Unable to submit lead");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="shell">
      <header className="hero">
        <div>
          <span className="eyebrow">Phase 1 vertical slice</span>
          <h1>Lead qualification<br /><em>with its reasoning intact.</em></h1>
          <p>Submit a website lead, normalize it into the canonical record, and inspect the deterministic decision trail.</p>
        </div>
        <div className="status-badge"><span /> Local foundation · no external integrations</div>
      </header>

      <section className="workspace">
        <form className="lead-form panel" onSubmit={submit}>
          <div className="panel-heading"><div><span className="eyebrow">01 / Intake</span><h2>Submit a test lead</h2></div></div>
          <div className="form-grid">
            <Field label="Full name" value={form.full_name} onChange={(value) => update("full_name", value)} required />
            <Field label="Email" type="email" value={form.email} onChange={(value) => update("email", value)} required />
            <Field label="Company" value={form.company_name} onChange={(value) => update("company_name", value)} required />
            <Field label="Job title" value={form.job_title ?? ""} onChange={(value) => update("job_title", value)} />
            <Field label="Industry" value={form.industry ?? ""} onChange={(value) => update("industry", value)} />
            <Field label="Geography" value={form.geography ?? ""} onChange={(value) => update("geography", value)} />
            <Field label="Employees" type="number" value={form.employee_count ?? ""} onChange={(value) => update("employee_count", value ? Number(value) : null)} />
            <Field label="Budget (USD)" type="number" value={form.budget ?? ""} onChange={(value) => update("budget", value ? Number(value) : null)} />
            <Field label="Product or service" value={form.product_or_service} onChange={(value) => update("product_or_service", value)} required />
          </div>
          <label className="field full-width"><span>Inquiry</span><textarea value={form.message} onChange={(event) => update("message", event.target.value)} placeholder="Describe the problem, urgency, budget, and desired next step..." required rows={6} /></label>
          {error && <p className="error">{error}</p>}
          <button disabled={submitting} type="submit">{submitting ? "Processing…" : "Qualify lead →"}</button>
        </form>

        <section className="results panel">
          <div className="panel-heading"><div><span className="eyebrow">02 / Decision trail</span><h2>{selected ? "Qualification result" : "Select a lead"}</h2></div></div>
          {selected ? <><div className="normalized"><span className="eyebrow">Normalized canonical data</span><div className="normalized-grid"><Data label="Name" value={selected.full_name} /><Data label="Company" value={selected.company_name} /><Data label="Source" value={`${selected.source} · ${selected.source_detail ?? ""}`} /><Data label="Contact" value={selected.email} /><Data label="Product" value={selected.product_or_service} /><Data label="Normalization" value={`v${selected.normalization_version}`} /></div><p className="message-preview">“{selected.message}”</p></div><DecisionPanel lead={selected} /></> : <div className="empty-state">New submissions will appear here with every scoring component and routing reason.</div>}
        </section>
      </section>

      <section className="lead-list panel"><div className="panel-heading"><div><span className="eyebrow">03 / Local records</span><h2>Recent leads</h2></div><span className="count">{leads.length} records</span></div><div className="records">{leads.length === 0 ? <p className="muted">No leads yet. Seed data can be loaded from the backend command.</p> : leads.map((lead) => <button className={`record ${selected?.id === lead.id ? "active" : ""}`} key={lead.id} onClick={() => setSelected(lead)}><span><b>{lead.full_name}</b><small>{lead.company_name} · {lead.source}</small></span><span className={`pill ${lead.qualification?.classification ?? ""}`}>{lead.qualification ? `${lead.qualification.total_score} · ${label(lead.qualification.classification)}` : "Pending"}</span></button>)}</div></section>
    </main>
  );
}

function Field({ label: fieldLabel, value, onChange, type = "text", required = false }: { label: string; value: string | number; onChange: (value: string) => void; type?: string; required?: boolean }) {
  return <label className="field"><span>{fieldLabel}</span><input type={type} value={value} onChange={(event) => onChange(event.target.value)} required={required} /></label>;
}

function Data({ label: dataLabel, value }: { label: string; value: string }) {
  return <div><span>{dataLabel}</span><b>{value || "Not provided"}</b></div>;
}

export default App;
