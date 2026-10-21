import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { createLead, listIntegrations, listLeads } from "../api";
import { CallsPanel } from "../components/CallsPanel";
import { DirectDialer } from "../components/DirectDialer";
import { opsGet } from "../operationsApi";
import type { IntegrationCard, Lead, LeadPayload } from "../types";
import { CallInsights, hasCallInsightData, type CallInsightsData } from "../components/CallInsights";
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
  insights: CallInsightsData | null;
  lead_id: string | null;
  lead_name: string | null;
  company_name: string | null;
  representative_name: string | null;
  history_page?: number;
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

export function DialerView({
  onOpenIntegrations,
  onOpenLead,
  selectedSection = "",
  canViewLeads,
  canEditLeads,
  canCall,
  canCallLeads,
  canViewIntegrations,
  preferredDialerId,
  onPreferredDialerChange,
  dialerCallActive,
  ringCentralEmbedReady,
}: {
  onOpenIntegrations: () => void;
  onOpenLead: (leadId: string) => void;
  selectedSection?: string;
  canViewLeads: boolean;
  canEditLeads: boolean;
  canCall: boolean;
  canCallLeads: boolean;
  canViewIntegrations: boolean;
  preferredDialerId: string;
  onPreferredDialerChange: (connectionId: string) => void;
  dialerCallActive: boolean;
  ringCentralEmbedReady: boolean;
}) {
  const [cards, setCards] = useState<IntegrationCard[]>([]);
  const [activity, setActivity] = useState<CallActivity | null>(null);
  const [tab, setTab] = useState<"overview" | "history" | "analysis">("overview");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [loadingDetails, setLoadingDetails] = useState<string | null>(null);
  const [loadedDetails, setLoadedDetails] = useState<Record<string, boolean>>({});
  const [leadSearch, setLeadSearch] = useState("");
  const [leadResults, setLeadResults] = useState<Lead[]>([]);
  const [selectedLead, setSelectedLead] = useState<Lead | null>(null);
  const [leadSearchLoading, setLeadSearchLoading] = useState(false);
  const [leadSearchError, setLeadSearchError] = useState("");
  const callPanelRef = useRef<HTMLDivElement>(null);
  const shouldFocusCallPanel = useRef(false);

  useEffect(() => {
    if (selectedSection === "history" || selectedSection === "analysis" || selectedSection === "overview") {
      setTab(selectedSection);
    }
  }, [selectedSection]);

  useEffect(() => {
    if (!selectedLead || !shouldFocusCallPanel.current) return;
    shouldFocusCallPanel.current = false;
    callPanelRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [selectedLead]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    Promise.all([
      listIntegrations(),
      opsGet<CallActivity>("/v1/calling/cloudtalk/recent", controller.signal),
    ])
      .then(([integrationCards, recentCalls]) => {
        setCards(integrationCards);
        setActivity({
          ...recentCalls,
          records: recentCalls.records.map((record) => ({ ...record, history_page: 1 })),
        });
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load dialer workspace");
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!canViewLeads) return;
    const controller = new AbortController();
    setLeadSearchLoading(true);
    setLeadSearchError("");
    const timeout = window.setTimeout(() => {
      void listLeads(
        { limit: 20, search: leadSearch.trim() || undefined },
        controller.signal,
      )
        .then((result) => {
          if (!controller.signal.aborted) setLeadResults(result.items);
        })
        .catch((reason) => {
          if (!controller.signal.aborted) {
            setLeadSearchError(
              reason instanceof Error ? reason.message : "Unable to search leads.",
            );
          }
        })
        .finally(() => {
          if (!controller.signal.aborted) setLeadSearchLoading(false);
        });
    }, 200);
    return () => {
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [canViewLeads, leadSearch]);

  async function refreshCalls() {
    setRefreshing(true);
    setError("");
    try {
      const refreshed = await opsGet<CallActivity>("/v1/calling/cloudtalk/recent");
      setLoadedDetails({});
      setActivity({
        ...refreshed,
        records: refreshed.records.map((record) => ({ ...record, history_page: 1 })),
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to load CloudTalk calls");
    } finally {
      setRefreshing(false);
    }
  }

  async function loadCloudTalkDetails(record: CallRecord) {
    if (loadingDetails) return;
    setLoadingDetails(record.id);
    setError("");
    try {
      const details = await opsGet<CallRecord>(
        `/v1/calling/cloudtalk/recent/${encodeURIComponent(record.id)}/details?page=${record.history_page ?? 1}`,
      );
      setActivity((current) => current ? {
        ...current,
        records: current.records.map((item) => item.id === record.id
          ? {
              ...item,
              transcript: details.transcript,
              recording_url: details.recording_url,
              insights: details.insights,
            }
          : item),
      } : current);
      setLoadedDetails((current) => ({ ...current, [record.id]: true }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to load CloudTalk analysis");
    } finally {
      setLoadingDetails(null);
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
        records: [...current.records, ...next.records.map((record) => ({ ...record, history_page: nextPage }))],
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
  const verifiedDialers = useMemo(
    () => dialers.flatMap((card) => card.connections
      .filter((connection) => connection.is_active && connection.status === "verified")
      .map((connection) => ({
        id: connection.id,
        provider: card.provider,
        account_name: connection.account_name,
      }))),
    [dialers],
  );
  const potentialLeadCount = activity?.records.filter((record) => assessLeadPotential(record).label === "Potential lead").length ?? 0;

  return (
    <div className="page-view dialer-page">
      <header className="dialer-head">
        <div>
          <span className="eyebrow">Calling operations</span>
          <h1>Dialer workspace</h1>
          <p>Place confirmed calls to leads or new numbers, then review provider-supplied recordings and transcripts.</p>
        </div>
        {canViewIntegrations && <button className="primary-button" onClick={onOpenIntegrations}>Manage dialer connections <span aria-hidden="true">→</span></button>}
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
                    {!isConnected && canViewIntegrations && <button className="dialer-provider-action" onClick={onOpenIntegrations}>Set up</button>}
                    {!isConnected && !canViewIntegrations && <small className="dialer-provider-action-hint">Ask an admin to connect this provider.</small>}
                  </article>;
                })}
              </div>
            </section>
            <section className="dialer-panel dialer-call-launch">
              <div className="dialer-panel-head">
                <div><span className="eyebrow">Direct calling</span><h2>Call a lead</h2></div>
                <span className="dialer-panel-count">Each call needs rep confirmation</span>
              </div>
              <p className="panel-copy">Choose a lead or press Call on a card. Corral uses the phone number saved on that lead, then asks you to confirm before dialing and checks workspace consent.</p>
              {!canViewLeads ? (
                <p className="dialer-empty">Lead-view permission is required to choose a contact.</p>
              ) : (
                <>
                  {selectedLead ? (
                    <>
                      <div className="dialer-selected-lead">
                        <div>
                          <strong>{selectedLead.full_name}</strong>
                          <span>{selectedLead.company_name || "Company not supplied"} · {selectedLead.phone || "No phone number"}</span>
                        </div>
                        <div className="dialer-selected-actions">
                          <button type="button" className="button-quiet" onClick={() => onOpenLead(selectedLead.id)}>Open lead</button>
                          <button type="button" className="button-quiet" onClick={() => setSelectedLead(null)}>Choose another lead</button>
                        </div>
                      </div>
                      <div ref={callPanelRef}>
                        <CallsPanel
                          key={selectedLead.id}
                          leadId={selectedLead.id}
                          leadName={selectedLead.full_name}
                          companyName={selectedLead.company_name}
                          leadPhone={selectedLead.phone}
                          representativeName={selectedLead.routing?.assigned_rep_name}
                          canCall={canCallLeads}
                          canEdit={canEditLeads}
                        />
                      </div>
                    </>
                  ) : (
                    <>
                      <label className="dialer-lead-search">
                        Search leads by name, company, or email
                        <input
                          type="search"
                          value={leadSearch}
                          onChange={(event) => setLeadSearch(event.target.value)}
                          placeholder="Search leads…"
                        />
                      </label>
                      {leadSearchError && <p className="team-alert" role="alert">{leadSearchError}</p>}
                      {leadSearchLoading ? (
                        <p role="status">Searching leads…</p>
                      ) : leadResults.length ? (
                        <div className="dialer-lead-results" aria-label="Lead search results">
                          {leadResults.map((lead) => (
                            <article className="dialer-lead-option" key={lead.id}>
                              <button
                                type="button"
                                className="dialer-lead-option-main"
                                onClick={() => setSelectedLead(lead)}
                                aria-label={`View call options for ${lead.full_name}`}
                              >
                                <span>
                                  <strong>{lead.full_name}</strong>
                                  <small>{lead.company_name || "Company not supplied"} · {lead.phone || "No phone number"}</small>
                                </span>
                                <span aria-hidden="true">→</span>
                              </button>
                              <button
                                type="button"
                                className="dialer-lead-call-action"
                                disabled={!lead.phone || !canCallLeads || dialerCallActive}
                                title={
                                  !lead.phone
                                    ? "Add a phone number to this lead before calling."
                                    : dialerCallActive
                                      ? "End the active call before starting another."
                                      : !canCallLeads
                                        ? "Calling requires Dialer, lead-view, and lead-edit permissions."
                                        : `Open confirmation to call ${lead.full_name}.`
                                }
                                onClick={() => {
                                  shouldFocusCallPanel.current = true;
                                  setSelectedLead(lead);
                                }}
                              >
                                {lead.phone ? "Call" : "No number"}
                              </button>
                            </article>
                          ))}
                        </div>
                      ) : (
                        <p className="dialer-empty">{leadSearch.trim() ? "No matching leads found." : "No leads are available to call yet."}</p>
                      )}
                    </>
                  )}
                </>
              )}
            </section>
            <DirectDialer
              connections={verifiedDialers}
              canCall={canCall}
              canViewLeads={canViewLeads}
              canEditLeads={canEditLeads}
              onOpenLead={onOpenLead}
              preferredConnectionId={preferredDialerId}
              onPreferredConnectionChange={onPreferredDialerChange}
              phoneCallActive={dialerCallActive}
              ringCentralEmbedReady={ringCentralEmbedReady}
            />
            <section className="dialer-workflow-grid">
              <article><span className="dialer-step">01</span><div><h3>Confirm before dialing</h3><p>Corral-started calls are placed individually with consent and permission checks. Calls started from CloudTalk Phone’s own keypad do not pass through those checks.</p></div></article>
              <article><span className="dialer-step">02</span><div><h3>Sync provider activity</h3><p>Import status, duration, recording links and transcripts from the connected provider.</p></div></article>
              <article><span className="dialer-step">03</span><div><h3>Review the conversation</h3><p>Use the history and transcript views to prepare follow-ups and qualification decisions.</p></div></article>
            </section>
          </>}

          {(tab === "history" || tab === "analysis") && <section className="dialer-panel dialer-history-panel">
            <div className="dialer-panel-head"><div><span className="eyebrow">CloudTalk activity</span><h2>{tab === "history" ? "Recent calls" : "Call analysis"}</h2></div>
              <button className="button-quiet" onClick={() => void refreshCalls()} disabled={refreshing}>{refreshing ? "Refreshing…" : "Refresh calls"}</button>
            </div>
            {!activity?.connections.length ? <p className="dialer-empty">Connect and verify a CloudTalk account to view its recent calls.</p> : !activity.records.length ? <p className="dialer-empty">No recent CloudTalk calls were returned for this account.</p> : <div className="lead-table-wrap lead-browser call-table-wrap">
              <table className="lead-table call-history-table">
                <thead><tr><th>Lead / contact</th><th>Source</th><th>Duration</th><th>Status</th><th>Representative</th><th>Received</th><th>Actions</th></tr></thead>
                <tbody>{activity.records.map((record) => {
                const detailsLoaded = Boolean(loadedDetails[record.id]);
                const hasInsights = hasCallInsightData(record.insights);
                const hasTranscript = Boolean(record.transcript?.trim());
                return <Fragment key={record.id}>
                  <tr>
                    <td className="lead-identity-cell" data-label="Lead / contact"><b>{record.lead_name || "Unknown contact"}</b><span>{record.company_name || record.customer_phone} · {statusLabel(record.direction)}</span></td>
                    <td data-label="Source">{providerLabel[record.provider] || statusLabel(record.provider)}</td>
                    <td data-label="Duration">{record.duration_seconds !== null ? `${record.duration_seconds}s` : "—"}</td>
                    <td data-label="Status"><span className="call-status">{statusLabel(record.status)}</span></td>
                    <td data-label="Representative">{record.representative_name || "Unassigned"}</td>
                    <td data-label="Received">{new Date(record.started_at || record.created_at).toLocaleString()}</td>
                    <td data-label="Actions"><div className="call-row-actions">
                      {record.lead_id && <button className="button-quiet" onClick={() => onOpenLead(record.lead_id!)}>Open lead to call</button>}
                      {!detailsLoaded && <button className="button-quiet" onClick={() => void loadCloudTalkDetails(record)} disabled={loadingDetails !== null}>{loadingDetails === record.id ? "Loading…" : "Load AI insights"}</button>}
                      {!record.lead_id && <AddLeadForm record={record} onCreated={(lead: Lead) => {
                  setActivity((current) => current ? {
                    ...current,
                    records: current.records.map((item) => item.id === record.id
                      ? { ...item, lead_id: lead.id, lead_name: lead.full_name }
                      : item),
                  } : current);
                }} />}
                    </div></td>
                  </tr>
                  {loadingDetails === record.id && <tr className="call-detail-row"><td colSpan={7}><p className="call-detail-note" role="status">Loading transcript and call insights…</p></td></tr>}
                  {detailsLoaded && <tr className="call-detail-row"><td colSpan={7} className="call-detail-cell">
                    {!hasTranscript && !hasInsights && <p className="call-detail-note">No transcript or AI findings are available for this call.</p>}
                    {!hasTranscript && hasInsights && <p className="call-detail-note">No transcript is available; showing provider-generated insights.</p>}
                    {hasInsights && <CallInsights insights={record.insights} />}
                    {record.recording_url && <a href={record.recording_url} target="_blank" rel="noopener noreferrer">Open provider recording or call details</a>}
                    {hasTranscript && <details className="dialer-transcript"><summary>View transcript</summary><pre>{record.transcript}</pre></details>}
                    {(hasTranscript || hasInsights) && <div className="dialer-lead-assessment"><strong>{assessLeadPotential(record).label}</strong><span>{assessLeadPotential(record).detail}</span></div>}
                  </td></tr>}
                </Fragment>;
              })}</tbody>
              </table>
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
