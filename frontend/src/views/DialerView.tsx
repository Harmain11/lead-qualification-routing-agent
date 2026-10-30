import { useEffect, useMemo, useState } from "react";
import { createLead, listIntegrations, listLeads } from "../api";
import { CallsPanel } from "../components/CallsPanel";
import { CallReviewDetails } from "../components/CallReviewDetails";
import type { CallInsightsData } from "../components/CallInsights";
import { opsGet, opsSave } from "../operationsApi";
import type { IntegrationCard, Lead, LeadPayload } from "../types";
import { callOutcome, formatCallDuration, type CallOutcome } from "../callOutcome";
import { Icon, type IconName } from "../v3/ui/Icon";
import { Badge, Banner, Button, EmptyState, IconButton, Menu, MenuItem, Monogram, PageHeader, Segmented, Sheet, type Tone } from "../v3/ui/primitives";

type CallRecord = {
  id: string;
  external_id?: string | null;
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
  outcome?: CallOutcome;
  linked?: boolean;
  history_page?: number;
  source: "stored" | "cloudtalk";
};

type CloudTalkActivity = {
  connections: Array<{ id: string; provider: string; account_name: string }>;
  records: Omit<CallRecord, "source">[];
  page: number;
  has_more: boolean;
};

type HistoryPage = { records: Omit<CallRecord, "source">[]; has_more: boolean; next_before: string | null };
type Filter = "all" | "inbound" | "outbound" | "missed";

const PROVIDER_LABEL: Record<string, string> = {
  twilio: "Twilio", aircall: "Aircall", ringcentral: "RingCentral", vapi: "Vapi", retell: "Retell AI", cloudtalk: "CloudTalk",
};
const OUTCOME: Record<CallOutcome, { label: string; tone: Tone }> = {
  answered: { label: "Answered", tone: "tint" },
  missed: { label: "Missed", tone: "danger" },
  no_answer: { label: "No answer", tone: "attention" },
  failed: { label: "Failed", tone: "danger" },
  in_progress: { label: "In progress", tone: "info" },
};

function directionIcon(record: CallRecord): IconName {
  const outcome = record.outcome ?? callOutcome(record.status, record.direction, record.duration_seconds);
  if (outcome === "missed") return "callMissed";
  return record.direction.toLowerCase() === "inbound" ? "callIn" : "callOut";
}

function callTime(record: CallRecord) {
  const date = new Date(record.started_at || record.created_at);
  if (Number.isNaN(date.getTime())) return "";
  const today = new Date();
  return date.toDateString() === today.toDateString()
    ? date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
    : date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

export function DialerView({
  onOpenIntegrations,
  onOpenLead,
  canViewLeads,
  canEditLeads,
  canCallLeads,
  canViewIntegrations,
  dialerCallActive,
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
  const [stored, setStored] = useState<CallRecord[]>([]);
  const [storedMore, setStoredMore] = useState<string | null>(null);
  const [cloudTalk, setCloudTalk] = useState<CloudTalkActivity | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [loadingDetails, setLoadingDetails] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [dialing, setDialing] = useState(false);
  const [callLead, setCallLead] = useState<Lead | null>(null);
  const [addFrom, setAddFrom] = useState<CallRecord | null>(null);
  const [linkFrom, setLinkFrom] = useState<CallRecord | null>(null);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    Promise.allSettled([
      listIntegrations(),
      opsGet<HistoryPage>("/v1/calling/history?limit=50", controller.signal),
      opsGet<CloudTalkActivity>("/v1/calling/cloudtalk/recent", controller.signal),
    ]).then(([integrationResult, historyResult, cloudTalkResult]) => {
      if (controller.signal.aborted) return;
      if (integrationResult.status === "fulfilled") setCards(integrationResult.value);
      if (historyResult.status === "fulfilled") {
        setStored(historyResult.value.records.map((record) => ({ ...record, source: "stored" as const })));
        setStoredMore(historyResult.value.has_more ? historyResult.value.next_before : null);
      } else {
        setError(historyResult.reason instanceof Error ? historyResult.reason.message : "Call history couldn't be loaded.");
      }
      if (cloudTalkResult.status === "fulfilled") {
        setCloudTalk({ ...cloudTalkResult.value, records: cloudTalkResult.value.records.map((record) => ({ ...record, history_page: 1 })) });
      }
    }).finally(() => {
      if (!controller.signal.aborted) { setLoading(false); setRefreshing(false); }
    });
    return () => controller.abort();
  }, [revision]);

  const records = useMemo(() => {
    const seen = new Set(stored.map((record) => record.external_id).filter(Boolean));
    const fromCloudTalk: CallRecord[] = (cloudTalk?.records ?? [])
      .filter((record) => !seen.has(record.id))
      .map((record) => ({ ...record, source: "cloudtalk" as const, outcome: callOutcome(record.status, record.direction, record.duration_seconds), linked: Boolean(record.lead_id) }));
    const all = [...stored, ...fromCloudTalk].sort((a, b) =>
      new Date(b.started_at || b.created_at).getTime() - new Date(a.started_at || a.created_at).getTime());
    const term = search.trim().toLowerCase();
    const digits = term.replace(/\D/g, "");
    return all.filter((record) => {
      const outcome = record.outcome ?? callOutcome(record.status, record.direction, record.duration_seconds);
      if (filter === "missed" && outcome !== "missed") return false;
      if ((filter === "inbound" || filter === "outbound") && record.direction.toLowerCase() !== filter) return false;
      if (!term) return true;
      return [record.lead_name, record.company_name].some((value) => value?.toLowerCase().includes(term))
        || (digits.length >= 3 && record.customer_phone.replace(/\D/g, "").includes(digits));
    });
  }, [stored, cloudTalk, filter, search]);

  const missedCount = useMemo(
    () => [...stored, ...(cloudTalk?.records ?? [])].filter((record) => callOutcome(record.status, record.direction, record.duration_seconds) === "missed").length,
    [stored, cloudTalk],
  );
  const dialers = useMemo(() => cards.filter((card) => /dialer|voice/i.test(card.category)), [cards]);
  const connected = dialers.filter((card) => card.connections.some((connection) => connection.is_active && ["connected", "verified"].includes(connection.status)));

  async function loadOlder() {
    if (!storedMore) return;
    setRefreshing(true);
    try {
      const next = await opsGet<HistoryPage>(`/v1/calling/history?limit=50&before=${encodeURIComponent(storedMore)}`);
      setStored((current) => [...current, ...next.records.map((record) => ({ ...record, source: "stored" as const }))]);
      setStoredMore(next.has_more ? next.next_before : null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Older calls couldn't be loaded.");
    } finally {
      setRefreshing(false);
    }
  }

  async function toggleDetails(record: CallRecord) {
    if (expanded === record.id) { setExpanded(null); return; }
    if (record.source === "cloudtalk" && !record.transcript && !record.insights) {
      setLoadingDetails(record.id);
      try {
        const details = await opsGet<CallRecord>(`/v1/calling/cloudtalk/recent/${encodeURIComponent(record.id)}/details?page=${record.history_page ?? 1}`);
        setCloudTalk((current) => current ? {
          ...current,
          records: current.records.map((item) => item.id === record.id ? { ...item, transcript: details.transcript, recording_url: details.recording_url, insights: details.insights } : item),
        } : current);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "The call transcript couldn't be loaded.");
        return;
      } finally {
        setLoadingDetails(null);
      }
    }
    setExpanded(record.id);
  }

  async function linkToLead(record: CallRecord, lead: Lead) {
    if (record.source === "cloudtalk") {
      const connectionId = cloudTalk?.connections[0]?.id;
      if (!connectionId) throw new Error("The CloudTalk connection is unavailable. Refresh and try again.");
      await opsSave(`/v1/calling/cloudtalk/recent/${encodeURIComponent(record.id)}/assign`, { connection_id: connectionId, lead_id: lead.id, page: record.history_page ?? 1 });
    } else {
      await opsSave(`/v1/calling/calls/${encodeURIComponent(record.id)}/link`, { lead_id: lead.id });
    }
    setNotice(`Call linked to ${lead.full_name || lead.company_name}.`);
    setRevision((value) => value + 1);
  }

  async function copyNumber(record: CallRecord) {
    try {
      await navigator.clipboard.writeText(record.customer_phone);
      setNotice(`Copied ${record.customer_phone}.`);
    } catch {
      setNotice("Couldn't copy. Select the number to copy it.");
    }
  }

  return (
    <div className="calls">
      <PageHeader
        title="Calls"
        subtitle={connected.length ? `${connected.map((card) => card.display_name).join(", ")} connected` : "Call history across your phone providers"}
        actions={<>
          <Button icon="refresh" onClick={() => { setRefreshing(true); setRevision((value) => value + 1); }} disabled={refreshing}>{refreshing ? "Refreshing…" : "Refresh"}</Button>
          {canViewLeads && <Button kind="filled" icon="phone" onClick={() => { setCallLead(null); setDialing(true); }} disabled={dialerCallActive}>New call</Button>}
        </>}
      />
      {error && <Banner tone="danger" title={error} action={<Button kind="plain" size="sm" onClick={() => setError("")}>Dismiss</Button>} />}
      {notice && <Banner title={notice} action={<Button kind="plain" size="sm" onClick={() => setNotice("")}>Dismiss</Button>} />}

      {!loading && connected.length === 0 && (
        <EmptyState icon="phone" title="Connect a phone provider" action={canViewIntegrations ? <Button kind="filled" onClick={onOpenIntegrations}>Connect a provider</Button> : undefined}>
          {canViewIntegrations ? "Connect CloudTalk, Aircall, Twilio, RingCentral, Vapi or Retell to call leads and keep every call in one history." : "Ask an admin to connect a phone provider."}
        </EmptyState>
      )}

      <section className="calls-history" aria-label="Call history">
        <div className="calls-toolbar">
          <Segmented<Filter>
            label="Show calls"
            value={filter}
            onChange={setFilter}
            options={[
              { value: "all", label: "All" },
              { value: "inbound", label: "Inbound" },
              { value: "outbound", label: "Outbound" },
              { value: "missed", label: missedCount ? `Missed · ${missedCount}` : "Missed" },
            ]}
          />
          <label className="calls-search">
            <Icon name="search" size={15} />
            <span className="v-sr">Search calls</span>
            <input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search name, company or number" />
          </label>
        </div>

        {loading && <div className="mailbox-skeleton" role="status" aria-label="Loading calls"><i /><i /><i /><i /></div>}
        {!loading && records.length === 0 && (
          <p className="mailbox-empty">{search ? "No calls match your search." : filter === "missed" ? "No missed calls. Nice." : "No calls yet. Calls you place or receive appear here."}</p>
        )}
        <ul className="calls-list">
          {records.map((record) => {
            const outcome = record.outcome ?? callOutcome(record.status, record.direction, record.duration_seconds);
            const name = record.lead_name || record.company_name || record.customer_phone || "Unknown caller";
            const known = Boolean(record.lead_id);
            const open = expanded === record.id;
            return (
              <li key={`${record.source}-${record.id}`} className="calls-row" data-outcome={outcome}>
                <span className="calls-direction" aria-hidden="true"><Icon name={directionIcon(record)} size={16} /></span>
                {known ? <Monogram text={name} size={34} /> : <span className="calls-unknown" aria-hidden="true"><Icon name="phone" size={15} /></span>}
                <div className="calls-who">
                  <b>{name}</b>
                  <small>
                    {known && record.company_name && record.lead_name ? `${record.company_name} · ` : ""}
                    {record.customer_phone}
                    {!known && " · Not in Corral"}
                    {known && !record.linked && " · Matched by number"}
                  </small>
                </div>
                <div className="calls-meta">
                  <Badge tone={OUTCOME[outcome].tone}>{OUTCOME[outcome].label}</Badge>
                  <small className="num">{record.direction.toLowerCase() === "inbound" ? "Inbound" : "Outbound"} · {formatCallDuration(record.duration_seconds)} · {PROVIDER_LABEL[record.provider] ?? record.provider}</small>
                </div>
                <time className="calls-time" dateTime={record.started_at || record.created_at}>{callTime(record)}</time>
                <div className="calls-actions">
                  {known && canCallLeads && (
                    <IconButton icon="phone" label={`Call ${name}`} disabled={dialerCallActive} onClick={() => onOpenLead(record.lead_id!)} />
                  )}
                  <IconButton icon="copy" label={`Copy ${record.customer_phone}`} onClick={() => void copyNumber(record)} disabled={!record.customer_phone} />
                  <Menu label={`More actions for ${name}`} iconOnly="more" trigger={null}>
                    {(close) => (
                      <>
                        {known && canViewLeads && <MenuItem icon="person" onSelect={() => { close(); onOpenLead(record.lead_id!); }}>Open lead</MenuItem>}
                        {!record.linked && canEditLeads && canViewLeads && <MenuItem icon="link" onSelect={() => { close(); setLinkFrom(record); }}>{known ? "Confirm link to this lead…" : "Link to a lead…"}</MenuItem>}
                        {!known && canEditLeads && <MenuItem icon="plus" onSelect={() => { close(); setAddFrom(record); }}>Add as a new lead…</MenuItem>}
                        <MenuItem icon="note" onSelect={() => { close(); void toggleDetails(record); }}>{open ? "Hide transcript" : "Transcript and insights"}</MenuItem>
                      </>
                    )}
                  </Menu>
                </div>
                {loadingDetails === record.id && <p className="calls-detail" role="status">Loading transcript…</p>}
                {open && <div className="calls-detail">
                  <CallReviewDetails transcript={record.transcript} insights={record.insights} recordingUrl={record.recording_url} message={record.message} />
                </div>}
              </li>
            );
          })}
        </ul>
        {storedMore && <Button className="mailbox-more" onClick={() => void loadOlder()} disabled={refreshing}>Load older calls</Button>}
      </section>

      {dialing && (
        <Sheet title={callLead ? `Call ${callLead.full_name || callLead.company_name}` : "New call"} onClose={() => setDialing(false)} width={620}>
          {callLead ? (
            <div className="calls-dial">
              <Button kind="plain" size="sm" icon="chevronLeft" onClick={() => setCallLead(null)}>Choose another lead</Button>
              <CallsPanel
                key={callLead.id}
                leadId={callLead.id}
                leadName={callLead.full_name}
                companyName={callLead.company_name}
                leadPhone={callLead.phone}
                representativeName={callLead.routing?.assigned_rep_name}
                canCall={canCallLeads}
                canEdit={canEditLeads}
              />
            </div>
          ) : (
            <LeadPicker onPick={setCallLead} requirePhone placeholder="Search a lead to call" />
          )}
        </Sheet>
      )}
      {linkFrom && (
        <Sheet title="Link call to a lead" subtitle={`${linkFrom.customer_phone} · ${callTime(linkFrom)}`} onClose={() => setLinkFrom(null)} width={520}>
          <LeadPicker
            placeholder="Search name, company, email or phone"
            initialSearch={linkFrom.lead_name ?? ""}
            onPick={(lead) => {
              const record = linkFrom;
              setLinkFrom(null);
              void linkToLead(record, lead).catch((reason) => setError(reason instanceof Error ? reason.message : "The call couldn't be linked."));
            }}
          />
        </Sheet>
      )}
      {addFrom && <AddLeadSheet record={addFrom} onClose={() => setAddFrom(null)} onCreated={async (lead) => { await linkToLead(addFrom, lead); setAddFrom(null); }} />}
    </div>
  );
}

function LeadPicker({ onPick, requirePhone = false, placeholder, initialSearch = "" }: { onPick: (lead: Lead) => void; requirePhone?: boolean; placeholder: string; initialSearch?: string }) {
  const [search, setSearch] = useState(initialSearch);
  const [leads, setLeads] = useState<Lead[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    const timeout = window.setTimeout(() => {
      listLeads({ limit: 20, search: search.trim() || undefined }, controller.signal)
        .then((result) => { if (!controller.signal.aborted) { setLeads(result.items); setError(""); } })
        .catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Leads couldn't be searched."); })
        .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, 180);
    return () => { window.clearTimeout(timeout); controller.abort(); };
  }, [search]);
  return (
    <div className="calls-picker">
      <label className="calls-search">
        <Icon name="search" size={15} />
        <span className="v-sr">{placeholder}</span>
        <input type="search" autoFocus value={search} onChange={(event) => setSearch(event.target.value)} placeholder={placeholder} />
      </label>
      {error && <Banner tone="danger" title={error} />}
      {loading && leads.length === 0 ? <p className="mailbox-empty" role="status">Searching…</p> : leads.length === 0 ? <p className="mailbox-empty">No matching leads.</p> : (
        <ul className="calls-picker-list">
          {leads.map((lead) => {
            const disabled = requirePhone && !lead.phone;
            return <li key={lead.id}>
              <button type="button" disabled={disabled} onClick={() => onPick(lead)} title={disabled ? "Add a phone number to this lead first" : undefined}>
                <Monogram text={lead.company_name || lead.full_name} size={30} />
                <span><b>{lead.full_name || lead.company_name}</b><small>{[lead.company_name, lead.phone || (requirePhone ? "No phone number" : lead.email)].filter(Boolean).join(" · ")}</small></span>
                <Icon name="chevronRight" size={14} />
              </button>
            </li>;
          })}
        </ul>
      )}
    </div>
  );
}

function AddLeadSheet({ record, onClose, onCreated }: { record: CallRecord; onClose: () => void; onCreated: (lead: Lead) => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [created, setCreated] = useState<Lead | null>(null);
  const [draft, setDraft] = useState({ full_name: "", email: "", company_name: "", product_or_service: "", message: record.insights?.summary || record.transcript || "" });
  const set = (key: keyof typeof draft, value: string) => setDraft((current) => ({ ...current, [key]: value }));
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    let lead = created;
    try {
      if (!lead) {
        const payload: LeadPayload = {
          source: "phone_inquiry",
          source_detail: `${PROVIDER_LABEL[record.provider] ?? record.provider} call ${record.id}`,
          full_name: draft.full_name, email: draft.email, phone: record.customer_phone || "", job_title: "",
          company_name: draft.company_name, industry: "", employee_count: null, annual_revenue: null,
          geography: "", budget: null, product_or_service: draft.product_or_service, message: draft.message,
        };
        lead = await createLead(payload);
        setCreated(lead);
      }
      await onCreated(lead);
    } catch (reason) {
      const detail = reason instanceof Error ? reason.message : "Please try again.";
      setError(lead ? `The lead was created, but the call couldn't be linked: ${detail}` : detail);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Sheet
      title="Add lead from call"
      subtitle={`${record.customer_phone} · ${callTime(record)}`}
      onClose={onClose}
      width={560}
      footer={<>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button kind="filled" type="submit" form="add-lead-from-call" disabled={busy}>{busy ? "Saving…" : created ? "Retry linking" : "Add lead"}</Button>
      </>}
    >
      <form id="add-lead-from-call" className="calls-form" onSubmit={(event) => void submit(event)}>
        <label className="v-field"><span>Name</span><input required value={draft.full_name} onChange={(event) => set("full_name", event.target.value)} /></label>
        <label className="v-field"><span>Email</span><input type="email" required value={draft.email} onChange={(event) => set("email", event.target.value)} /></label>
        <label className="v-field"><span>Company</span><input required value={draft.company_name} onChange={(event) => set("company_name", event.target.value)} /></label>
        <label className="v-field"><span>What they need</span><input required value={draft.product_or_service} onChange={(event) => set("product_or_service", event.target.value)} /></label>
        <label className="v-field calls-form-wide"><span>Call notes</span><textarea required rows={4} value={draft.message} onChange={(event) => set("message", event.target.value)} /></label>
        {error && <div className="calls-form-wide"><Banner tone="danger" title={error} /></div>}
      </form>
    </Sheet>
  );
}
