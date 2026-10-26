import { Fragment, useEffect, useRef, useState, type FormEvent } from "react";
import { opsGet, opsSave } from "../operationsApi";
import { createClientId } from "../clientIds";
import { PHONE_CALL_ENDED } from "./phoneBridge";
import { CallInsights, hasCallInsightData, type CallInsightsData } from "./CallInsights";

const providerNames: Record<string, string> = { twilio: "Twilio", aircall: "Aircall", ringcentral: "RingCentral", vapi: "Vapi", retell: "Retell AI", cloudtalk: "CloudTalk" };
type CallRecord = { id: string; connection_id: string; provider: string; external_id: string | null; status: string; direction: string; customer_phone: string; started_at: string | null; created_at: string; duration_seconds: number | null; transcript: string | null; recording_url: string | null; message: string | null; insights: CallInsightsData };
type Activity = { connections: { id: string; provider: string; account_name: string }[]; records: CallRecord[]; outreach_allowed: boolean; outreach_reason: string };
type CloudTalkNumberOptions = { numbers: string[]; default_number: string | null };
type CloudTalkHandoff = { phone_number: string; caller_id: string; handoff_url: string; message: string };

export function CallsPanel({ leadId, leadName, companyName, representativeName, leadPhone, canCall, canEdit }: { leadId: string; leadName?: string; companyName?: string; representativeName?: string | null; leadPhone?: string | null; canCall: boolean; canEdit: boolean }) {
  const [data, setData] = useState<Activity | null>(null);
  const [connectionId, setConnectionId] = useState("");
  const [repPhone, setRepPhone] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [requestId, setRequestId] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [cloudTalkNumbers, setCloudTalkNumbers] = useState<string[]>([]);
  const [cloudTalkNumbersLoading, setCloudTalkNumbersLoading] = useState(false);
  const [cloudTalkNumbersError, setCloudTalkNumbersError] = useState("");
  const [callerId, setCallerId] = useState("");
  const [cloudTalkHandoff, setCloudTalkHandoff] = useState<CloudTalkHandoff | null>(null);
  const [handoffOpened, setHandoffOpened] = useState(false);
  const [loadingDetails, setLoadingDetails] = useState<string | null>(null);
  const [loadedDetails, setLoadedDetails] = useState<Record<string, boolean>>({});
  const callFormRef = useRef<HTMLFormElement>(null);
  const [callRevision, setCallRevision] = useState(0);
  useEffect(() => {
    const ended = () => setCallRevision((value) => value + 1);
    window.addEventListener(PHONE_CALL_ENDED, ended);
    return () => window.removeEventListener(PHONE_CALL_ENDED, ended);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    opsGet<Activity>(`/v1/calling/leads/${leadId}`, controller.signal).then((value) => {
      if (controller.signal.aborted) return;
      setData(value);
      setConnectionId((current) => value.connections.some((item) => item.id === current) ? current : value.connections.length === 1 ? value.connections[0].id : "");
    }).catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Call activity could not be loaded."); });
    return () => controller.abort();
  }, [leadId, callRevision]);
  const selected = data?.connections.find((item) => item.id === connectionId);
  const voiceAgent = selected?.provider === "vapi" || selected?.provider === "retell";
  const needsRep = selected?.provider === "twilio";
  useEffect(() => {
    if (selected?.provider !== "cloudtalk" || !connectionId) {
      setCloudTalkNumbers([]);
      setCloudTalkNumbersLoading(false);
      setCloudTalkNumbersError("");
      setCallerId("");
      setCloudTalkHandoff(null);
      setHandoffOpened(false);
      return;
    }
    const controller = new AbortController();
    setCloudTalkNumbers([]);
    setCloudTalkNumbersError("");
    setCallerId("");
    setCloudTalkHandoff(null);
    setHandoffOpened(false);
    setCloudTalkNumbersLoading(true);
    opsGet<CloudTalkNumberOptions>(
      `/v1/calling/cloudtalk/numbers?connection_id=${encodeURIComponent(connectionId)}`,
      controller.signal,
    )
      .then((options) => {
        if (controller.signal.aborted) return;
        setCloudTalkNumbers(options.numbers);
        setCallerId(
          options.default_number && options.numbers.includes(options.default_number)
            ? options.default_number
            : options.numbers[0] ?? "",
        );
      })
      .catch((reason) => {
        if (!controller.signal.aborted) {
          setCloudTalkNumbersError(
            reason instanceof Error ? reason.message : "CloudTalk caller IDs could not be loaded.",
          );
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setCloudTalkNumbersLoading(false);
      });
    return () => controller.abort();
  }, [connectionId, selected?.provider]);
async function refresh() { setLoadedDetails({}); setData(await opsGet<Activity>(`/v1/calling/leads/${leadId}`)); }
  async function start(event: FormEvent) {
    event.preventDefault();
    if (!confirmed || !connectionId || busy || (selected?.provider === "cloudtalk" && !callerId)) return;
    setBusy(true); setError(""); setNotice("");
    try {
      if (selected?.provider === "cloudtalk") {
        const handoff = await opsSave<CloudTalkHandoff>(
          `/v1/calling/leads/${leadId}/cloudtalk-handoff`,
          {
            connection_id: connectionId,
            request_id: createClientId(),
            confirmed: true,
            caller_id: callerId,
          },
        );
        setCloudTalkHandoff(handoff);
        setHandoffOpened(false);
        setConfirmed(false);
        return;
      }
      const id = requestId || createClientId();
      setRequestId(id);
      const result = await opsSave<CallRecord>(`/v1/calling/leads/${leadId}/start`, { connection_id: connectionId, request_id: id, confirmed: true, rep_phone: repPhone || null });
      setNotice(result.message || "Call request recorded.");
      setConfirmed(false);
      // Keep the same id for an unresolved submission; this button then reads
      // the durable receipt rather than creating a second provider call.
      if (!["submitting", "uncertain"].includes(result.status)) setRequestId("");
      await refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Call request could not be confirmed. Check your provider before placing another call."); }
    finally { setBusy(false); }
  }
  async function sync() {
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await opsSave<{ message: string }>(`/v1/calling/leads/${leadId}/sync`, { connection_id: connectionId });
      await refresh(); setNotice(result.message);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Call history could not be refreshed."); }
    finally { setBusy(false); }
  }
  async function loadCallDetails(call: CallRecord) {
    if (loadingDetails || !call.external_id) return;
    setLoadingDetails(call.id); setError("");
    try {
      const details = await opsGet<CallRecord>(`/v1/calling/leads/${leadId}/calls/${call.id}/details`);
      setData((current) => current ? { ...current, records: current.records.map((item) => item.id === call.id ? details : item) } : current);
      setLoadedDetails((current) => ({ ...current, [call.id]: true }));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Call insights could not be loaded."); }
    finally { setLoadingDetails(null); }
  }
  function prepareCallAgain(call: CallRecord) {
    setConnectionId(call.connection_id);
    setConfirmed(false);
    callFormRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  }
  return <section className="ops-panel calls-panel" aria-labelledby="calls-heading">
    <div className="ops-toolbar calls-panel-toolbar">
      <div className="calls-panel-heading">
        <span className="calls-heading-mark" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M7 4.8 9.2 8l-1.5 1.6a14 14 0 0 0 6.7 6.7L16 14.8l3.2 2.2-.7 3a1.5 1.5 0 0 1-1.7 1.1A17 17 0 0 1 3 7.3 1.5 1.5 0 0 1 4.1 5.6L7 4.8Z" />
            <path d="M14 5a5 5 0 0 1 5 5" />
            <path d="M14 2a8 8 0 0 1 8 8" />
          </svg>
        </span>
        <div className="calls-heading-copy">
          <span className="eyebrow">Calling &amp; voice agents</span>
          <h3 id="calls-heading">Calls &amp; voice activity</h3>
          <p className="calls-lead-number">{leadPhone ? `Lead number: ${leadPhone}` : "Add an international phone number to this lead before calling."}</p>
        </div>
      </div>
      {canEdit && data && data.connections.length > 0 && <button type="button" className="secondary-button calls-sync" disabled={!connectionId || !leadPhone || busy} onClick={() => void sync()}>{busy ? "Working…" : "Sync recent calls"}</button>}
    </div>
    {error && <p className="notice calls-notice" role="alert">{error}</p>}
    {notice && <p className="notice calls-notice calls-notice-success" role="status">{notice}</p>}
    {!data && !error && <p className="calls-loading" role="status">Loading call activity…</p>}
    {data && data.connections.length === 0 && <div className="ops-empty calls-provider-empty"><strong>No verified calling provider</strong><p>Connect and verify Twilio, Aircall, RingCentral, Vapi, Retell AI or CloudTalk in Integrations to call from this lead.</p><a href="?view=integrations">Manage calling integrations →</a></div>}
    {data && data.connections.length > 0 && <>
      {!data.outreach_allowed && <p className="notice calls-notice calls-consent-notice" role="status">{data.outreach_reason}</p>}
      <form ref={callFormRef} onSubmit={(event) => void start(event)} className="calls-form">
        <div className="ops-form-grid calls-form-grid">
          <label>Calling provider<select value={connectionId} disabled={busy || Boolean(requestId)} onChange={(event) => { setConnectionId(event.target.value); setConfirmed(false); }}><option value="">Choose a verified provider</option>{data.connections.map((item) => <option value={item.id} key={item.id}>{providerNames[item.provider]} · {item.account_name}</option>)}</select></label>
          {selected && ["twilio", "ringcentral"].includes(selected.provider) && <label>Your rep number{needsRep ? " (required)" : " (optional override)"}<input type="tel" placeholder="+14155550123" value={repPhone} required={needsRep} disabled={busy || Boolean(requestId)} onChange={(event) => { setRepPhone(event.target.value); setConfirmed(false); }} autoComplete="tel" /><small>{needsRep ? "Twilio rings you first, then connects the lead." : "Leave blank to ring the rep number saved in Integrations."}</small></label>}
          {selected?.provider === "cloudtalk" && <label>CloudTalk caller ID
            <select
              value={callerId}
              disabled={busy || cloudTalkNumbersLoading || Boolean(requestId)}
              onChange={(event) => {
                setCallerId(event.target.value);
                setCloudTalkHandoff(null);
                setHandoffOpened(false);
                setConfirmed(false);
              }}
            >
              <option value="">
                {cloudTalkNumbersLoading ? "Loading assigned caller IDs…" : "Choose an assigned caller ID"}
              </option>
              {cloudTalkNumbers.map((number) => <option value={number} key={number}>{number}</option>)}
            </select>
            <small>
              {cloudTalkNumbers.length
                ? "Only caller IDs assigned to this CloudTalk agent can be used."
                : cloudTalkNumbersLoading
                  ? "Checking caller IDs assigned to this CloudTalk agent."
                  : "No caller IDs are assigned to this CloudTalk agent. Ask an admin to update its CloudTalk setup."}
            </small>
          </label>}
        </div>
        {selected && <p className="calls-provider-note">{voiceAgent ? "This uses the assistant and phone number configured in your provider account. The saved voice agent talks to the lead." : selected.provider === "aircall" ? "The selected Aircall user must be signed in and available in Aircall Workspace." : selected.provider === "cloudtalk" ? "After confirmation, Corral prepares a CloudTalk Desktop handoff with this lead’s saved number. It does not dial through the embedded phone." : "Answer your rep phone to connect to the lead."}</p>}
        {selected?.provider === "cloudtalk" && <>
          {cloudTalkNumbersError && <p className="notice calls-notice" role="alert">{cloudTalkNumbersError}</p>}
          {cloudTalkHandoff && <div className="direct-call-handoff" role="status">
            <div>
              <strong>Ready to open CloudTalk Desktop</strong>
              <p>{cloudTalkHandoff.message}</p>
              <small>Lead number: {cloudTalkHandoff.phone_number} · Caller ID: {cloudTalkHandoff.caller_id}</small>
              <small>After the call, use Sync recent calls to bring the activity into Corral.</small>
            </div>
            {handoffOpened ? (
              <small>Handoff opened. Check CloudTalk Desktop before retrying to avoid placing a duplicate call.</small>
            ) : (
              <a
                className="primary-button"
                href={cloudTalkHandoff.handoff_url}
                onClick={() => setHandoffOpened(true)}
              >
                Open CloudTalk Desktop
              </a>
            )}
          </div>}
        </>}
        {canCall && <div className="calls-action-group">
          <label className="ops-check calls-confirm">
            <input type="checkbox" checked={confirmed} disabled={busy || !data.outreach_allowed || !leadPhone || !selected || (selected.provider === "cloudtalk" && (cloudTalkNumbersLoading || !callerId))} onChange={(event) => { setConfirmed(event.target.checked); setCloudTalkHandoff(null); setHandoffOpened(false); }} />
            <span>
              <strong>Confirm this individual call</strong>
              <small>I confirm this individual {voiceAgent ? "voice-agent " : ""}call to the lead.</small>
            </span>
          </label>
          <button className="calls-submit" type="submit" disabled={busy || !selected || !confirmed || !leadPhone || !data.outreach_allowed || (needsRep && !repPhone) || (selected.provider === "cloudtalk" && (!callerId || cloudTalkNumbersLoading))}>{busy ? "Submitting…" : requestId ? "Check existing call request" : selected?.provider === "cloudtalk" ? "Prepare CloudTalk Desktop call" : voiceAgent ? "Start voice-agent call" : data.records.length ? "Call again" : "Call lead"}</button>
        </div>}
        {!canCall && <p className="calls-permission-note">Calling this lead requires the Dialer calling permission plus lead-view and lead-edit access. Ask an admin to grant any missing permission.</p>}
      </form>
    </>}
    {data && <div className="calls-history">
      <div className="calls-history-head"><h4>Recent activity</h4></div>
      {data.records.length ? <div className="lead-table-wrap lead-browser call-table-wrap">
       <table className="lead-table call-history-table">
         <thead><tr><th>Lead / contact</th><th>Source</th><th>Duration</th><th>Status</th><th>Representative</th><th>Received</th><th>Actions</th></tr></thead>
         <tbody>{data.records.map((call) => {
           const detailsLoaded = Boolean(loadedDetails[call.id]);
           const hasTranscript = Boolean(call.transcript?.trim());
           const hasInsights = hasCallInsightData(call.insights);
           return <Fragment key={call.id}>
             <tr>
               <td className="lead-identity-cell" data-label="Lead / contact"><b>{leadName || "Lead"}</b><span>{companyName || call.customer_phone} · {call.direction.replaceAll("_", " ")}</span></td>
               <td data-label="Source">{providerNames[call.provider] || call.provider}</td>
               <td data-label="Duration">{call.duration_seconds !== null ? `${call.duration_seconds}s` : "—"}</td>
               <td data-label="Status"><span className="call-status">{call.status.replaceAll("_", " ")}</span></td>
               <td data-label="Representative">{representativeName || "Unassigned"}</td>
               <td data-label="Received">{new Date(call.started_at || call.created_at).toLocaleString()}</td>
               <td data-label="Actions"><div className="call-row-actions">
                 {canCall && <button type="button" className="button-quiet" disabled={!data.outreach_allowed || busy} onClick={() => prepareCallAgain(call)}>Call again</button>}
                 {!detailsLoaded && call.external_id && <button type="button" className="button-quiet" disabled={loadingDetails !== null} onClick={() => void loadCallDetails(call)}>{loadingDetails === call.id ? "Loading…" : call.provider === "cloudtalk" ? "Load AI insights" : "Load call details"}</button>}
               </div></td>
             </tr>
             {loadingDetails === call.id && <tr className="call-detail-row"><td colSpan={7}><p role="status" className="call-detail-note">Loading transcript and call insights…</p></td></tr>}
             {detailsLoaded && <tr className="call-detail-row"><td colSpan={7} className="call-detail-cell">
               {!hasTranscript && !hasInsights && <p className="call-detail-note">No transcript or AI findings are available for this call.</p>}
               {!hasTranscript && hasInsights && <p className="call-detail-note">No transcript is available; showing provider-generated insights.</p>}
               {hasInsights && <CallInsights insights={call.insights} />}
               {call.recording_url && <a href={call.recording_url} target="_blank" rel="noopener noreferrer">Open provider recording or call details</a>}
               {hasTranscript && <details><summary>View transcript</summary><pre>{call.transcript}</pre></details>}
               {call.message && <p className="call-record-receipt">{call.message}</p>}
             </td></tr>}
           </Fragment>;
         })}</tbody>
       </table>
      </div> : <div className="calls-history-empty">
        <span aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
            <path d="M7 4.8 9.2 8l-1.5 1.6a14 14 0 0 0 6.7 6.7L16 14.8l3.2 2.2-.7 3a1.5 1.5 0 0 1-1.7 1.1A17 17 0 0 1 3 7.3 1.5 1.5 0 0 1 4.1 5.6L7 4.8Z" />
          </svg>
        </span>
        <span>
          <strong>No call activity recorded for this lead</strong>
          <small>Use Sync recent calls to import matching provider history.</small>
        </span>
      </div>}
    </div>}
    <small className="calls-footnote">History sync reads recent provider calls that match this lead number. AI insights load only when requested. Calls always require confirmation and pass consent checks.</small>
  </section>;
}
