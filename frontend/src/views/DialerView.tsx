import { useEffect, useMemo, useState } from "react";
import { listIntegrations, listLeads } from "../api";
import { opsGet } from "../operationsApi";
import type { IntegrationCard, Lead } from "../types";
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
};

type CallActivity = {
  connections: Array<{ id: string; provider: string; account_name: string }>;
  records: CallRecord[];
  outreach_allowed: boolean;
  outreach_reason: string | null;
};

const providerLabel: Record<string, string> = {
  twilio: "Twilio",
  aircall: "Aircall",
  ringcentral: "RingCentral",
  vapi: "Vapi",
  retell: "Retell AI",
};

function statusLabel(status: string) {
  return status.replaceAll("_", " ").replace(/\b\w/g, (value) => value.toUpperCase());
}

export function DialerView({ onOpenIntegrations, selectedSection = "" }: { onOpenIntegrations: () => void; selectedSection?: string }) {
  const [cards, setCards] = useState<IntegrationCard[]>([]);
  const [leads, setLeads] = useState<Lead[]>([]);
  const [selectedLead, setSelectedLead] = useState("");
  const [activity, setActivity] = useState<CallActivity | null>(null);
  const [tab, setTab] = useState<"overview" | "history" | "analysis">("overview");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (selectedSection === "history" || selectedSection === "analysis" || selectedSection === "overview") {
      setTab(selectedSection);
    }
  }, [selectedSection]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    Promise.all([listIntegrations(), listLeads({ limit: 50 }, controller.signal)])
      .then(([integrationCards, leadPage]) => {
        setCards(integrationCards);
        setLeads(leadPage.items);
        setSelectedLead((current) => current || leadPage.items[0]?.id || "");
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load dialer workspace");
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!selectedLead) { setActivity(null); return; }
    const controller = new AbortController();
    opsGet<CallActivity>(`/v1/calling/leads/${encodeURIComponent(selectedLead)}`, controller.signal)
      .then(setActivity)
      .catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load call history"); });
    return () => controller.abort();
  }, [selectedLead]);

  const dialers = useMemo(
    () => cards.filter((card) => /dialer|voice/i.test(card.category)),
    [cards],
  );
  const connected = dialers.filter((card) => card.connections.some((connection) => connection.is_active && ["connected", "verified", "configured"].includes(connection.status)));
  const transcriptCount = activity?.records.filter((record) => Boolean(record.transcript)).length ?? 0;
  const selectedLeadRecord = leads.find((lead) => lead.id === selectedLead);

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
            <article><span>Lead records ready</span><strong>{leads.length}</strong><small>Contacts with call context</small></article>
            <article><span>Transcripts available</span><strong>{transcriptCount}</strong><small>For the selected lead</small></article>
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
            <div className="dialer-panel-head"><div><span className="eyebrow">Lead activity</span><h2>{tab === "history" ? "Call history & transcripts" : "Transcript analysis"}</h2></div>
              <label className="dialer-lead-select"><span>Lead</span><select value={selectedLead} onChange={(event) => setSelectedLead(event.target.value)}><option value="">Choose a lead</option>{leads.map((lead) => <option value={lead.id} key={lead.id}>{lead.full_name}{lead.phone ? ` · ${lead.phone}` : ""}</option>)}</select></label>
            </div>
            {!selectedLead ? <p className="dialer-empty">Choose a lead to review its call activity.</p> : !activity?.records.length ? <p className="dialer-empty">No provider call activity is recorded for {selectedLeadRecord?.full_name || "this lead"} yet.</p> : <div className="dialer-record-list">
              {activity.records.map((record) => <article className="dialer-record" key={record.id}>
                <div className="dialer-record-head"><div><strong>{providerLabel[record.provider] || record.provider}</strong><span>{new Date(record.started_at || record.created_at).toLocaleString()} · {record.direction.replaceAll("_", " ")}{record.duration_seconds !== null ? ` · ${record.duration_seconds}s` : ""}</span></div><b>{statusLabel(record.status)}</b></div>
                {tab === "analysis" ? <div className="dialer-analysis-row"><span className={record.transcript ? "dialer-analysis-good" : ""}>{record.transcript ? "Transcript ready" : "Transcript unavailable"}</span><span>{record.recording_url ? "Recording ready" : "No recording"}</span><span>{record.message || "Provider returned no additional message."}</span></div> : <>{record.message && <p>{record.message}</p>}{record.recording_url && <a href={record.recording_url} target="_blank" rel="noopener noreferrer">Open recording ↗</a>}{record.transcript && <details><summary>View transcript</summary><pre>{record.transcript}</pre></details>}{!record.transcript && !record.recording_url && <small>Transcripts and recordings appear when the connected provider supplies them.</small>}</>}
              </article>)}
            </div>}
          </section>}
        </>
      )}
    </div>
  );
}
