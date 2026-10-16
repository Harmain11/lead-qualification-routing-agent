import { useEffect, useMemo, useState } from "react";
import { createLead, listIntegrations } from "../api";
import { opsGet } from "../operationsApi";
import type { IntegrationCard, Lead, LeadPayload } from "../types";
import { CallInsights, type CallInsightsData } from "../components/CallInsights";
import "./DialerView.css";

type CallRecord = {
  id: string;
  provider: string;
  status: string;
  direction: string;
  customer_phone: string;
  started_at: string | null;
  created_at: string;
  duration_seconds: number | null;
  transcript: string | null;
  recording_url: string | null;
  message: string | null;
  insights: CallInsightsData;
  lead_id: string | null;
  lead_name: string | null;
};

type CallActivity = {
  connections: Array<{ id: string; provider: string; account_name: string }>;
  records: CallRecord[];
  page: number;
  has_more: boolean;
};

const providerLabel: Record<string, string> = {
  twilio: "Twilio",
  aircall: "Aircall",
  ringcentral: "RingCentral",
  vapi: "Vapi",
  retell: "Retell AI",
  cloudtalk: "CloudTalk",
};

function statusLabel(status: string) {
  return status.replaceAll("_", " ").replace(/\b\w/g, (value) => value.toUpperCase());
}

function assessLeadPotential(record: CallRecord) {
  const text = `${record.insights?.summary ?? ""} ${record.transcript ?? ""}`.toLowerCase();
  if (!text.trim()) {
    return { label: "Needs review", detail: "No call summary or transcript is available to assess." };
  }
  if (/\b(wrong number|not interested|remove me|do not call|don't call|spam|job application|recruitment)\b/.test(text)) {
    return { label: "Unlikely lead", detail: "The call content includes a clear non-prospect signal." };
  }
  if (/\b(pricing|quote|quotation|demo|buy|purchase|interested|looking for|proposal|budget|evaluate|follow[- ]?up|schedule|need a)\b/.test(text)) {
    return { label: "Potential lead", detail: "The call content includes buying intent or a concrete next step. Confirm before adding." };
  }
  return { label: "Review manually", detail: "The available call content does not show a clear buying signal either way." };
}

export function DialerView({ onOpenIntegrations, selectedSection = "" }: { onOpenIntegrations: () => void; selectedSection?: string }) {
  const [cards, setCards] = useState<IntegrationCard[]>([]);
  const [activity, setActivity] = useState<CallActivity | null>(null);
  const [tab, setTab] = useState<"overview" | "history" | "analysis">("overview");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (selectedSection === "history" || selectedSection === "analysis" || selectedSection === "overview") {
      setTab(selectedSection);
    }
  }, [selectedSection]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    Promise.all([
      listIntegrations(),
      opsGet<CallActivity>("/v1/calling/cloudtalk/recent", controller.signal),
    ])
      .then(([integrationCards, recentCalls]) => {
        setCards(integrationCards);
        setActivity(recentCalls);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load dialer workspace");
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, []);

  async function refreshCalls() {
    setRefreshing(true);
    setError("");
    try {
      setActivity(await opsGet<CallActivity>("/v1/calling/cloudtalk/recent"));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to load CloudTalk calls");
    } finally {
      setRefreshing(false);
    }
  }

  async function loadMoreCalls() {
    if (!activity?.has_more) return;
    setRefreshing(true);
    setError("");
    try {
      const nextPage = activity.page + 1;
      const next = await opsGet<CallActivity>(`/v1/calling/cloudtalk/recent?page=${nextPage}`);
      setActivity((current) => current ? {
        ...current,
        records: [...current.records, ...next.records],
        page: next.page,
        has_more: next.has_more,
      } : next);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to load older CloudTalk calls");
    } finally {
      setRefreshing(false);
    }
  }

  const dialers = useMemo(
    () => cards.filter((card) => /dialer|voice/i.test(card.category)),
    [cards],
  );
  const connected = dialers.filter((card) => card.connections.some((connection) => connection.is_active && ["connected", "verified", "configured"].includes(connection.status)));
  const potentialLeadCount = activity?.records.filter((record) => assessLeadPotential(record).label === "Potential lead").length ?? 0;

  return (
    <div className="page-view dialer-page">
      <header className="dialer-head">
        <div>
          <span className="eyebrow">Calling operations</span>
          <h1>Dialer workspace</h1>
          <p>Connect a provider, place confirmed calls from lead records, and review provider-supplied recordings and transcripts in one place.</p>
        </div>
        <button className="primary-button" onClick={onOpenIntegrations}>Manage dialer connections <span aria-hidden="true">→</span></button>
      </header>

      <nav className="dialer-tabs" aria-label="Dialer workspace sections">
        {([["overview", "Overview"], ["history", "Call history"], ["analysis", "Transcript analysis"]] as const).map(([key, label]) => (
          <button key={key} className={tab === key ? "is-active" : ""} aria-pressed={tab === key} onClick={() => setTab(key)}>{label}</button>
        ))}
      </nav>

      {error && <p className="team-alert" role="alert">{error}</p>}
      {loading ? <p role="status">Loading dialer workspace…</p> : (
        <>
          <section className="dialer-summary-grid" aria-label="Dialer status">
            <article><span>Connected providers</span><strong>{connected.length}</strong><small>Verified calling accounts</small></article>
            <article><span>Recent CloudTalk calls</span><strong>{activity?.records.length ?? 0}</strong><small>Latest calls from the connected account</small></article>
            <article><span>Potential leads</span><strong>{potentialLeadCount}</strong><small>Based on call summaries and transcripts</small></article>
          </section>

          {tab === "overview" && <>
            <section className="dialer-panel">
              <div className="dialer-panel-head"><div><span className="eyebrow">Provider setup</span><h2>Dialers & voice agents</h2></div><span className="dialer-panel-count">{dialers.length} providers</span></div>
              <div className="dialer-provider-grid">
                {dialers.map((card) => {
                  const connection = card.connections.find((item) => item.is_active) ?? card.connection;
                  const isConnected = Boolean(connection && ["connected", "verified", "configured"].includes(connection.status));
                  return <article className="dialer-provider-card" key={card.provider}>
                    <div className="dialer-provider-icon">{card.display_name.slice(0, 2).toUpperCase()}</div>
                    <div className="dialer-provider-copy"><h3>{card.display_name}</h3><p>{connection?.account_name || "No account connected"}</p></div>
                    <span className={`dialer-status ${isConnected ? "is-connected" : ""}`}>{isConnected ? "Connected" : "Not connected"}</span>
                    {!isConnected && <button className="dialer-provider-action" onClick={onOpenIntegrations}>Set up</button>}
                  </article>;
                })}
              </div>
            </section>
            <section className="dialer-workflow-grid">
              <article><span className="dialer-step">01</span><div><h3>Confirm before dialing</h3><p>Calls remain rep-confirmed and are launched from a lead with a valid international number.</p></div></article>
              <article><span className="dialer-step">02</span><div><h3>Sync provider activity</h3><p>Import status, duration, recording links and transcripts from the connected provider.</p></div></article>
              <article><span className="dialer-step">03</span><div><h3>Review the conversation</h3><p>Use the history and transcript views to prepare follow-ups and qualification decisions.</p></div></article>
            </section>
          </>}

          {(tab === "history" || tab === "analysis") && <section className="dialer-panel dialer-history-panel">
            <div className="dialer-panel-head"><div><span className="eyebrow">CloudTalk activity</span><h2>{tab === "history" ? "Recent calls" : "Call analysis"}</h2></div>
              <button className="button-quiet" onClick={() => void refreshCalls()} disabled={refreshing}>{refreshing ? "Refreshing…" : "Refresh calls"}</button>
            </div>
            {!activity?.connections.length ? <p className="dialer-empty">Connect and verify a CloudTalk account to view its recent calls.</p> : !activity.records.length ? <p className="dialer-empty">No recent CloudTalk calls were returned for this account.</p> : <div className="dialer-record-list">
              {activity.records.map((record) => <article className="dialer-record" key={record.id}>
                <div className="dialer-record-head"><div><strong>{record.lead_name || record.customer_phone || providerLabel[record.provider] || record.provider}</strong><span>{new Date(record.started_at || record.created_at).toLocaleString()} · {record.direction.replaceAll("_", " ")}{record.duration_seconds !== null ? ` · ${record.duration_seconds}s` : ""}</span></div><b>{statusLabel(record.status)}</b></div>
                {record.lead_id && <p className="dialer-linked-lead">Matched to existing lead: {record.lead_name}</p>}
                {tab === "analysis" ? <><div className="dialer-analysis-row"><span className={record.transcript ? "dialer-analysis-good" : ""}>{record.transcript ? "Transcript ready" : "Transcript unavailable"}</span><span>{record.recording_url ? "Recording or call details available" : "No recording or call details link"}</span><span>{record.message || "Provider returned no additional message."}</span></div>{record.provider === "cloudtalk" && <CallInsights insights={record.insights ?? null} />}{record.recording_url && <a href={record.recording_url} target="_blank" rel="noopener noreferrer">Open provider recording or call details ↗</a>}{record.transcript && <details><summary>View transcript</summary><pre>{record.transcript}</pre></details>}</> : <>{record.message && <p>{record.message}</p>}{record.provider === "cloudtalk" && <CallInsights insights={record.insights ?? null} />}{record.recording_url && <a href={record.recording_url} target="_blank" rel="noopener noreferrer">Open provider recording or call details ↗</a>}{record.transcript && <details><summary>View transcript</summary><pre>{record.transcript}</pre></details>}{!record.transcript && !record.recording_url && <small>Transcripts and recordings appear when the connected provider supplies them.</small>}</>}
                <div className="dialer-lead-assessment"><strong>{assessLeadPotential(record).label}</strong><span>{assessLeadPotential(record).detail}</span></div>
                {!record.lead_id && <AddLeadForm record={record} onCreated={(lead: Lead) => {
                  setActivity((current) => current ? {
                    ...current,
                    records: current.records.map((item) => item.id === record.id
                      ? { ...item, lead_id: lead.id, lead_name: lead.full_name }
                      : item),
                  } : current);
                }} />}
              </article>)}
              {activity.has_more && <button className="button-quiet" onClick={() => void loadMoreCalls()} disabled={refreshing}>{refreshing ? "Loading…" : "Load older calls"}</button>}
            </div>}
          </section>}
        </>
      )}
    </div>
  );
}

function AddLeadForm({ record, onCreated }: { record: CallRecord; onCreated: (lead: Lead) => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [draft, setDraft] = useState({
    full_name: "",
    email: "",
    company_name: "",
    product_or_service: "",
    message: record.insights?.summary || record.transcript || "",
  });
  const set = (key: keyof typeof draft, value: string) => setDraft((current) => ({ ...current, [key]: value }));
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const payload: LeadPayload = {
      source: "phone_inquiry",
      source_detail: `CloudTalk call ${record.id}`,
      full_name: draft.full_name,
      email: draft.email,
      phone: record.customer_phone || "",
      job_title: "",
      company_name: draft.company_name,
      industry: "",
      employee_count: null,
      annual_revenue: null,
      geography: "",
      budget: null,
      product_or_service: draft.product_or_service,
      message: draft.message,
    };
    try {
      const lead = await createLead(payload);
      onCreated(lead);
      setOpen(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to add this lead.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="dialer-add-lead">
      {!open ? <button className="button-quiet" onClick={() => setOpen(true)}>Add lead</button> : (
        <form onSubmit={(event) => void submit(event)}>
          <strong>Confirm lead details</strong>
          <label>Name<input required value={draft.full_name} onChange={(event) => set("full_name", event.target.value)} /></label>
          <label>Email<input type="email" required value={draft.email} onChange={(event) => set("email", event.target.value)} /></label>
          <label>Company<input required value={draft.company_name} onChange={(event) => set("company_name", event.target.value)} /></label>
          <label>Product or service<input required value={draft.product_or_service} onChange={(event) => set("product_or_service", event.target.value)} /></label>
          <label>Call notes<textarea required rows={3} value={draft.message} onChange={(event) => set("message", event.target.value)} /></label>
          {error && <p className="team-alert" role="alert">{error}</p>}
          <button type="button" className="button-quiet" onClick={() => setOpen(false)} disabled={busy}>Cancel</button>
          <button type="submit" className="primary-button" disabled={busy}>{busy ? "Adding…" : "Confirm and add lead"}</button>
        </form>
      )}
    </div>
  );
}
