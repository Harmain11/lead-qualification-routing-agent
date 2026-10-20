import { useEffect, useState, type FormEvent } from "react";
import { createLead } from "../api";
import { createClientId } from "../clientIds";
import type { CallInsightsData } from "./CallInsights";
import type { Lead, LeadPayload } from "../types";
import { opsGet, opsSave } from "../operationsApi";

export type DirectDialerConnection = {
  id: string;
  provider: string;
  account_name: string;
};

type DirectCallRecord = {
  id: string;
  lead_id: string | null;
  lead_name?: string | null;
  company_name?: string | null;
  connection_id: string;
  provider: string;
  external_id: string | null;
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
};

type CloudTalkHandoff = {
  phone_number: string;
  caller_id: string;
  handoff_url: string;
  message: string;
};

const providerNames: Record<string, string> = {
  twilio: "Twilio",
  aircall: "Aircall",
  ringcentral: "RingCentral",
  vapi: "Vapi",
  retell: "Retell AI",
  cloudtalk: "CloudTalk",
};

const keypadKeys = [
  { value: "1", letters: "" },
  { value: "2", letters: "ABC" },
  { value: "3", letters: "DEF" },
  { value: "4", letters: "GHI" },
  { value: "5", letters: "JKL" },
  { value: "6", letters: "MNO" },
  { value: "7", letters: "PQRS" },
  { value: "8", letters: "TUV" },
  { value: "9", letters: "WXYZ" },
  { value: "+", letters: "" },
  { value: "0", letters: "+" },
  { value: "backspace", letters: "" },
];

function acceptedCall(record: DirectCallRecord) {
  return !["failed", "uncertain", "submitting"].includes(record.status);
}

export function DirectDialer({
  connections,
  canCall,
  canViewLeads,
  canEditLeads,
  onOpenLead,
}: {
  connections: DirectDialerConnection[];
  canCall: boolean;
  canViewLeads: boolean;
  canEditLeads: boolean;
  onOpenLead: (leadId: string) => void;
}) {
  const [connectionId, setConnectionId] = useState("");
  const [phoneNumber, setPhoneNumber] = useState("");
  const [repPhone, setRepPhone] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [requestId, setRequestId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [result, setResult] = useState<DirectCallRecord | null>(null);
  const [history, setHistory] = useState<DirectCallRecord[]>([]);
  const [historyError, setHistoryError] = useState("");
  const [cloudTalkNumbers, setCloudTalkNumbers] = useState<string[]>([]);
  const [cloudTalkDefaultNumber, setCloudTalkDefaultNumber] = useState("");
  const [cloudTalkCallerId, setCloudTalkCallerId] = useState("");
  const [cloudTalkNumbersLoading, setCloudTalkNumbersLoading] = useState(false);
  const [cloudTalkNumbersError, setCloudTalkNumbersError] = useState("");
  const [cloudTalkHandoff, setCloudTalkHandoff] = useState<CloudTalkHandoff | null>(null);
  const [cloudTalkHandoffOpened, setCloudTalkHandoffOpened] = useState(false);

  useEffect(() => {
    if (!connections.some((connection) => connection.id === connectionId)) {
      setConnectionId(connections[0]?.id ?? "");
      setConfirmed(false);
      setCloudTalkHandoff(null);
      setNotice("");
    }
  }, [connections, connectionId]);

  async function refreshHistory() {
    if (!canViewLeads) return;
    try {
      const data = await opsGet<{ records: DirectCallRecord[] }>("/v1/calling/direct/recent");
      setHistory(data.records);
      setHistoryError("");
      setResult((current) => current
        ? data.records.find((record) => record.id === current.id) ?? current
        : current);
    } catch (reason) {
      setHistoryError(reason instanceof Error ? reason.message : "Direct call history could not be loaded.");
    }
  }

  useEffect(() => {
    void refreshHistory();
  }, [canViewLeads]);

  const selected = connections.find((connection) => connection.id === connectionId);
  const needsRep = selected?.provider === "twilio";

  useEffect(() => {
    if (selected?.provider !== "cloudtalk" || !connectionId) {
      setCloudTalkNumbers([]);
      setCloudTalkDefaultNumber("");
      setCloudTalkCallerId("");
      setCloudTalkNumbersError("");
      setCloudTalkNumbersLoading(false);
      setCloudTalkHandoff(null);
      setCloudTalkHandoffOpened(false);
      return;
    }
    const controller = new AbortController();
    setCloudTalkNumbers([]);
    setCloudTalkDefaultNumber("");
    setCloudTalkCallerId("");
    setCloudTalkNumbersError("");
    setCloudTalkNumbersLoading(true);
    opsGet<{ numbers: string[]; default_number: string | null }>(
      `/v1/calling/cloudtalk/numbers?connection_id=${encodeURIComponent(connectionId)}`,
      controller.signal,
    )
      .then((data) => {
        if (!controller.signal.aborted) {
          setCloudTalkNumbers(data.numbers);
          setCloudTalkDefaultNumber(data.default_number || "");
          setCloudTalkCallerId((current) => (
            data.numbers.includes(current)
              ? current
              : data.default_number && data.numbers.includes(data.default_number)
                ? data.default_number
                : data.numbers[0] || ""
          ));
        }
      })
      .catch((reason) => {
        if (!controller.signal.aborted) {
          setCloudTalkNumbersError(
            reason instanceof Error
              ? reason.message
              : "CloudTalk phone numbers could not be loaded.",
          );
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setCloudTalkNumbersLoading(false);
      });
    return () => controller.abort();
  }, [connectionId, selected?.provider]);

  function addPhoneKey(value: string) {
    setPhoneNumber((current) => {
      if (value === "+") {
        if (current.startsWith("+")) return current;
        return `+${current}`;
      }
      return `${current}${value}`;
    });
    setConfirmed(false);
    setCloudTalkHandoff(null);
    setNotice("");
  }

  function removePhoneKey() {
    setPhoneNumber((current) => current.slice(0, -1));
    setConfirmed(false);
    setCloudTalkHandoff(null);
    setNotice("");
  }

  function clearPhoneNumber() {
    setPhoneNumber("");
    setConfirmed(false);
    setCloudTalkHandoff(null);
    setNotice("");
  }

  async function startCall(event: FormEvent) {
    event.preventDefault();
    if (!canCall || !confirmed || !connectionId || busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const id = requestId || createClientId();
      setRequestId(id);
      if (selected?.provider === "cloudtalk") {
        const handoff = await opsSave<CloudTalkHandoff>(
          "/v1/calling/direct/cloudtalk-handoff",
          {
            connection_id: connectionId,
            request_id: id,
            phone_number: phoneNumber.trim(),
            caller_id: cloudTalkCallerId,
            confirmed: true,
            consent_confirmed: true,
          },
        );
        setCloudTalkHandoff(handoff);
        setCloudTalkHandoffOpened(false);
        setResult(null);
        setNotice("Pre-call checks passed. Open CloudTalk Desktop to place the call.");
        setConfirmed(false);
        setRequestId("");
        return;
      }
      const call = await opsSave<DirectCallRecord>("/v1/calling/direct/start", {
        connection_id: connectionId,
        request_id: id,
        phone_number: phoneNumber.trim(),
        rep_phone: repPhone.trim() || null,
        confirmed: true,
        consent_confirmed: true,
      });
      setResult(call);
      setNotice(call.message || "Call request recorded.");
      setConfirmed(false);
      if (!["submitting", "uncertain"].includes(call.status)) setRequestId("");
      await refreshHistory();
    } catch (reason) {
      if (selected?.provider === "cloudtalk") setRequestId("");
      setError(reason instanceof Error
        ? reason.message
        : "Call request could not be confirmed. Check the provider before retrying.");
    } finally {
      setBusy(false);
    }
  }

  function updateLead(record: DirectCallRecord, lead: Lead) {
    setHistory((current) => current.map((item) => item.id === record.id
      ? { ...item, lead_id: lead.id, lead_name: lead.full_name }
      : item));
    setResult((current) => current?.id === record.id
      ? { ...current, lead_id: lead.id, lead_name: lead.full_name }
      : current);
  }

  function renderLeadAction(record: DirectCallRecord) {
    if (record.lead_id) {
      return <button type="button" className="button-quiet" onClick={() => onOpenLead(record.lead_id!)}>
        Open {record.lead_name || "matched lead"}
      </button>;
    }
    if (!acceptedCall(record)) return null;
    if (!canEditLeads) return <small>Adding a lead requires lead edit permission.</small>;
    return <DirectCallLeadForm record={record} onCreated={(lead) => updateLead(record, lead)} />;
  }

  return <section className="dialer-panel direct-dialer-panel">
    <div className="dialer-panel-head">
      <div><span className="eyebrow">Direct calling</span><h2>Call a new number</h2></div>
      <span className="dialer-panel-count">One confirmed call at a time</span>
    </div>
    <p className="panel-copy">Choose a verified calling account and enter one number. Every call still requires an individual confirmation and remains subject to workspace consent and residency rules.</p>
    {!connections.length ? (
      <div className="direct-dialer-status is-unavailable" role="status">
        <span aria-hidden="true" />
        <div><strong>No verified calling account</strong><p>Connect and verify a calling provider in Integrations → Calling before dialing.</p></div>
      </div>
    ) : !canCall ? (
      <div className="direct-dialer-status is-unavailable" role="status">
        <span aria-hidden="true" />
        <div><strong>Calling permission unavailable</strong><p>Your role needs agent action permission to start a direct call.</p></div>
      </div>
    ) : (
      <form className="direct-dialer-form" onSubmit={(event) => void startCall(event)}>
        <div className="ops-form-grid direct-dialer-fields">
          <div className="direct-dialer-number-field">
            <label htmlFor="direct-dial-phone-number">Number to call</label>
            <input
              id="direct-dial-phone-number"
              type="tel"
              autoComplete="tel"
              value={phoneNumber}
              onChange={(event) => {
                setPhoneNumber(event.target.value);
                setConfirmed(false);
                setCloudTalkHandoff(null);
                setNotice("");
              }}
              placeholder="+14155550123"
              maxLength={32}
              required
              disabled={busy || Boolean(requestId)}
            />
            <small>Use +country code format, for example +14155550123.</small>
            <div className="direct-dialer-keypad">
              <div className="direct-dialer-keypad-head">
                <span>Dial pad</span>
                <button
                  type="button"
                  className="direct-dialer-clear"
                  onClick={clearPhoneNumber}
                  disabled={busy || Boolean(requestId) || !phoneNumber}
                >
                  Clear
                </button>
              </div>
              <div className="direct-dialer-keypad-grid" role="group" aria-label="Phone number keypad">
                {keypadKeys.map((key) => {
                  const backspace = key.value === "backspace";
                  return (
                    <button
                      type="button"
                      className={`direct-dialer-key${backspace ? " is-backspace" : ""}`}
                      key={key.value}
                      aria-label={backspace ? "Delete last digit" : key.value === "+" ? "Add country code plus sign" : `Add digit ${key.value}`}
                      onClick={() => backspace ? removePhoneKey() : addPhoneKey(key.value)}
                      disabled={busy || Boolean(requestId)}
                    >
                      {backspace ? (
                        <span aria-hidden="true">⌫</span>
                      ) : (
                        <>
                          <strong>{key.value}</strong>
                          {key.letters && <small>{key.letters}</small>}
                        </>
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
          <div className="direct-dialer-account-field">
            <label htmlFor="direct-dial-connection">Calling account</label>
            <select id="direct-dial-connection" value={connectionId} onChange={(event) => { setConnectionId(event.target.value); setConfirmed(false); setCloudTalkHandoff(null); setNotice(""); }} disabled={busy || Boolean(requestId)}>
              {connections.map((connection) => (
                <option value={connection.id} key={connection.id}>
                  {providerNames[connection.provider] || connection.provider} · {connection.account_name}
                </option>
              ))}
            </select>
            {selected?.provider === "cloudtalk" && (
              <div className="direct-dialer-caller-ids" aria-live="polite">
                <label htmlFor="direct-dial-cloudtalk-caller-id">Call from number</label>
                {cloudTalkNumbersLoading ? (
                  <p role="status">Loading assigned numbers…</p>
                ) : cloudTalkNumbersError ? (
                  <p role="alert">{cloudTalkNumbersError}</p>
                ) : cloudTalkNumbers.length ? (
                  <select
                    id="direct-dial-cloudtalk-caller-id"
                    value={cloudTalkCallerId}
                    onChange={(event) => {
                      setCloudTalkCallerId(event.target.value);
                      setConfirmed(false);
                      setCloudTalkHandoff(null);
                      setNotice("");
                    }}
                    disabled={busy || Boolean(requestId)}
                    required
                  >
                    <option value="" disabled>Select a caller ID</option>
                    {cloudTalkNumbers.map((number) => (
                      <option value={number} key={number}>
                        {number}{number === cloudTalkDefaultNumber ? " (default)" : ""}
                      </option>
                    ))}
                  </select>
                ) : (
                  <p role="status">No caller IDs are assigned to the CloudTalk agent in this connection.</p>
                )}
                <small>
                  Select a number assigned to this agent, and make sure CloudTalk Desktop is signed
                  into the same agent. Corral checks call permissions, then opens CloudTalk Desktop
                  with this caller ID. CloudTalk may dial immediately if its Click to Call setting
                  is enabled.
                </small>
              </div>
            )}
            {selected && ["twilio", "ringcentral"].includes(selected.provider) && (
              <label>Your rep number{needsRep ? " (required)" : " (optional override)"}
                <input
                  type="tel"
                  autoComplete="tel"
                  value={repPhone}
                  onChange={(event) => { setRepPhone(event.target.value); setConfirmed(false); }}
                  placeholder="+14155550222"
                  maxLength={32}
                  required={needsRep}
                  disabled={busy || Boolean(requestId)}
                />
                <small>{needsRep ? "Twilio rings you first, then connects the call." : "Leave blank to use the rep number saved in Integrations."}</small>
              </label>
            )}
            <div className={`direct-dialer-status${error ? " has-error" : notice ? " has-notice" : ""}`} role={error ? "alert" : "status"} aria-live="polite">
              <span aria-hidden="true" />
              <div>
                <strong>{error
                  ? selected?.provider === "cloudtalk"
                    ? "CloudTalk handoff could not be prepared"
                    : `${providerNames[selected?.provider ?? ""] || selected?.provider || "Calling provider"} could not start the call`
                  : busy
                    ? selected?.provider === "cloudtalk"
                      ? "Checking CloudTalk caller ID and call safeguards…"
                      : `Calling with ${providerNames[selected?.provider ?? ""] || selected?.provider || "the selected account"}…`
                    : notice
                      ? selected?.provider === "cloudtalk"
                        ? "CloudTalk handoff ready"
                        : `${providerNames[selected?.provider ?? ""] || selected?.provider || "Calling provider"} call update`
                      : "Verified account ready"}</strong>
                <p>{error || notice || `${selected?.account_name ?? "Selected account"} is verified and available for a confirmed call.`}</p>
              </div>
            </div>
          </div>
        </div>
        <label className="ops-check direct-dialer-confirm">
          <input
            type="checkbox"
            checked={confirmed}
            disabled={
              busy ||
              !phoneNumber.trim() ||
              !connectionId ||
              (selected?.provider === "cloudtalk" &&
                (cloudTalkNumbersLoading || !cloudTalkCallerId || Boolean(cloudTalkNumbersError)))
            }
            onChange={(event) => {
              setConfirmed(event.target.checked);
              setCloudTalkHandoff(null);
            }}
          />
          <span>
            <strong>
              I have permission to call this number and authorize this individual
              {selected?.provider === "cloudtalk"
                ? ` call from ${cloudTalkCallerId || "the selected number"}`
                : " call"}.
            </strong>
            <small>
              Corral checks workspace do-not-call, consent, and residency rules before
              {selected?.provider === "cloudtalk" ? " handoff." : " dialing."}
              {selected?.provider === "cloudtalk" ? " CloudTalk Desktop handles the call after handoff." : ""}
            </small>
          </span>
        </label>
        <div className="direct-dialer-actions">
          <small>
            {selected?.provider === "cloudtalk"
              ? "CloudTalk handles the call; its outcome will not automatically appear in Corral."
              : "Calls are not attached to a lead unless you explicitly add one after the call."}
          </small>
          <button
            type="submit"
            className="primary-button"
            disabled={
              busy ||
              !confirmed ||
              !phoneNumber.trim() ||
              !connectionId ||
              (needsRep && !repPhone.trim()) ||
              (selected?.provider === "cloudtalk" &&
                (!cloudTalkCallerId || cloudTalkNumbersLoading || Boolean(cloudTalkNumbersError)))
            }
          >
            {busy
              ? selected?.provider === "cloudtalk" ? "Checking…" : "Calling…"
              : requestId
                ? "Check existing call request"
                : selected?.provider === "cloudtalk" ? "Prepare CloudTalk call" : "Call number"}
          </button>
        </div>
      </form>
    )}
    {cloudTalkHandoff && selected?.provider === "cloudtalk" && (
      <div className="direct-call-handoff" role="status">
        <div>
          <strong>Ready to call {cloudTalkHandoff.phone_number} from {cloudTalkHandoff.caller_id}</strong>
          <p>{cloudTalkHandoff.message}</p>
        </div>
        <a
          className="primary-button"
          href={cloudTalkHandoff.handoff_url}
          target="_blank"
          rel="noreferrer"
          aria-disabled={cloudTalkHandoffOpened}
          aria-label={`Open CloudTalk Desktop to call ${cloudTalkHandoff.phone_number} from ${cloudTalkHandoff.caller_id}`}
          onClick={(event) => {
            if (cloudTalkHandoffOpened) {
              event.preventDefault();
              return;
            }
            setCloudTalkHandoffOpened(true);
          }}
        >
          {cloudTalkHandoffOpened ? "Handoff opened" : "Open CloudTalk Desktop"}
        </a>
        <small>Call details and results remain in CloudTalk and will not be added to Corral call history automatically.</small>
      </div>
    )}
    {result && <div className="direct-call-result">
      <div>
        <strong>{result.customer_phone}</strong>
        <span>{providerNames[result.provider] || result.provider} · {result.status.replaceAll("_", " ")}</span>
      </div>
      {result.message && <p>{result.message}</p>}
      {!history.some((record) => record.id === result.id) && renderLeadAction(result)}
    </div>}
    {canViewLeads && <div className="direct-call-history">
      <div className="direct-call-history-head">
        <h3>Recent direct calls</h3>
        <button type="button" className="button-quiet" onClick={() => void refreshHistory()}>Refresh</button>
      </div>
      {historyError && <p className="team-alert" role="alert">{historyError}</p>}
      {history.length === 0 ? <p className="dialer-empty">No direct-number calls have been recorded yet.</p> : (
        <ul>
          {history.map((record) => <li key={record.id}>
            <div className="direct-call-history-copy">
              <strong>{record.lead_name || record.customer_phone}</strong>
              <span>{providerNames[record.provider] || record.provider} · {record.status.replaceAll("_", " ")}</span>
              <small>{new Date(record.started_at || record.created_at).toLocaleString()}</small>
            </div>
            {record.id !== result?.id && renderLeadAction(record)}
          </li>)}
        </ul>
      )}
    </div>}
  </section>;
}

function DirectCallLeadForm({ record, onCreated }: { record: DirectCallRecord; onCreated: (lead: Lead) => void }) {
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

  function setField(key: keyof typeof draft, value: string) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const payload: LeadPayload = {
      source: "phone_inquiry",
      source_detail: `${providerNames[record.provider] || record.provider} direct call ${record.id}`,
      full_name: draft.full_name,
      email: draft.email,
      phone: record.customer_phone,
      job_title: "",
      company_name: draft.company_name,
      industry: "",
      employee_count: null,
      annual_revenue: null,
      geography: "",
      budget: null,
      product_or_service: draft.product_or_service,
      message: draft.message || record.message || "Manual outbound call.",
    };
    try {
      const lead = await createLead(payload);
      onCreated(lead);
      setOpen(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to add this number as a lead.");
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return <button type="button" className="button-quiet" onClick={() => setOpen(true)}>Add lead</button>;
  }
  return <form className="direct-call-lead-form" onSubmit={(event) => void submit(event)}>
    <strong>Confirm lead details for {record.customer_phone}</strong>
    <label>Name<input required value={draft.full_name} onChange={(event) => setField("full_name", event.target.value)} /></label>
    <label>Email<input type="email" required value={draft.email} onChange={(event) => setField("email", event.target.value)} /></label>
    <label>Company<input required value={draft.company_name} onChange={(event) => setField("company_name", event.target.value)} /></label>
    <label>Product or service<input required value={draft.product_or_service} onChange={(event) => setField("product_or_service", event.target.value)} /></label>
    <label>Call notes<textarea required rows={3} value={draft.message} onChange={(event) => setField("message", event.target.value)} /></label>
    {error && <p className="team-alert" role="alert">{error}</p>}
    <div className="direct-call-lead-actions">
      <button type="button" className="button-quiet" onClick={() => setOpen(false)} disabled={busy}>Cancel</button>
      <button type="submit" className="primary-button" disabled={busy}>{busy ? "Adding…" : "Confirm and add lead"}</button>
    </div>
  </form>;
}
