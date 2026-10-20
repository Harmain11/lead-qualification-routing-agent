import { Fragment, useEffect, useRef, useState, type FormEvent } from "react";
import { opsGet, opsSave } from "../operationsApi";
import { createClientId } from "../clientIds";
import { CallInsights, hasCallInsightData, type CallInsightsData } from "./CallInsights";

const providerNames: Record<string, string> = { twilio: "Twilio", aircall: "Aircall", ringcentral: "RingCentral", vapi: "Vapi", retell: "Retell AI", cloudtalk: "CloudTalk" };
type CallRecord = { id: string; connection_id: string; provider: string; external_id: string | null; status: string; direction: string; customer_phone: string; started_at: string | null; created_at: string; duration_seconds: number | null; transcript: string | null; recording_url: string | null; message: string | null; insights: CallInsightsData };
type Activity = { connections: { id: string; provider: string; account_name: string }[]; records: CallRecord[]; outreach_allowed: boolean; outreach_reason: string };

export function CallsPanel({ leadId, leadName, companyName, representativeName, leadPhone, canCall, canEdit }: { leadId: string; leadName?: string; companyName?: string; representativeName?: string | null; leadPhone?: string | null; canCall: boolean; canEdit: boolean }) {
  const [data, setData] = useState<Activity | null>(null);
  const [connectionId, setConnectionId] = useState("");
  const [repPhone, setRepPhone] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [requestId, setRequestId] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [loadingDetails, setLoadingDetails] = useState<string | null>(null);
  const [loadedDetails, setLoadedDetails] = useState<Record<string, boolean>>({});
  const callFormRef = useRef<HTMLFormElement>(null);
  useEffect(() => {
    const controller = new AbortController();
    opsGet<Activity>(`/v1/calling/leads/${leadId}`, controller.signal).then((value) => {
      if (controller.signal.aborted) return;
      setData(value);
      setConnectionId((current) => value.connections.some((item) => item.id === current) ? current : value.connections.length === 1 ? value.connections[0].id : "");
    }).catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Call activity could not be loaded."); });
    return () => controller.abort();
  }, [leadId]);
  const selected = data?.connections.find((item) => item.id === connectionId);
  const voiceAgent = selected?.provider === "vapi" || selected?.provider === "retell";
  const needsRep = selected?.provider === "twilio";
async function refresh() { setLoadedDetails({}); setData(await opsGet<Activity>(`/v1/calling/leads/${leadId}`)); }
  async function start(event: FormEvent) {
    event.preventDefault();
    if (!confirmed || !connectionId || busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
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
    <div className="ops-toolbar"><div><span className="eyebrow">Calling & voice agents</span><h3 id="calls-heading">Calls & voice activity</h3><p>{leadPhone ? `Lead number: ${leadPhone}` : "Add an international phone number to this lead before calling."}</p></div>
      {canEdit && data && data.connections.length > 0 && <button type="button" className="secondary-button" disabled={!connectionId || !leadPhone || busy} onClick={() => void sync()}>{busy ? "Working…" : "Sync recent calls"}</button>}
    </div>
    {error && <p className="notice" role="alert">{error}</p>}
    {notice && <p className="notice" role="status">{notice}</p>}
    {!data && !error && <p role="status">Loading call activity…</p>}
     {data && data.connections.length === 0 && <div className="ops-empty"><strong>No verified calling provider</strong><p>Connect and verify Twilio, Aircall, RingCentral, Vapi, Retell AI or CloudTalk in Integrations to call from this lead.</p><a href="?view=integrations">Manage calling integrations →</a></div>}
    {data && data.connections.length > 0 && <>
      {!data.outreach_allowed && <p className="notice" role="status">{data.outreach_reason}</p>}
      <form ref={callFormRef} onSubmit={(event) => void start(event)} className="calls-form">
        <div className="ops-form-grid">
          <label>Calling provider<select value={connectionId} disabled={busy || Boolean(requestId)} onChange={(event) => { setConnectionId(event.target.value); setConfirmed(false); }}><option value="">Choose a verified provider</option>{data.connections.map((item) => <option value={item.id} key={item.id}>{providerNames[item.provider]} · {item.account_name}</option>)}</select></label>
          {selected && ["twilio", "ringcentral"].includes(selected.provider) && <label>Your rep number{needsRep ? " (required)" : " (optional override)"}<input type="tel" placeholder="+14155550123" value={repPhone} required={needsRep} disabled={busy || Boolean(requestId)} onChange={(event) => { setRepPhone(event.target.value); setConfirmed(false); }} autoComplete="tel" /><small>{needsRep ? "Twilio rings you first, then connects the lead." : "Leave blank to ring the rep number saved in Integrations."}</small></label>}
        </div>
         {selected && <p>{voiceAgent ? "This uses the assistant and phone number configured in your provider account. The saved voice agent talks to the lead." : selected.provider === "aircall" ? "The selected Aircall user must be signed in and available in Aircall Workspace." : selected.provider === "cloudtalk" ? "CloudTalk rings the configured agent first, then connects the lead once the agent answers." : "Answer your rep phone to connect to the lead."}</p>}
        {canCall && <><label className="ops-check"><input type="checkbox" checked={confirmed} disabled={busy || !data.outreach_allowed || !leadPhone || !selected} onChange={(event) => setConfirmed(event.target.checked)} /><span>I confirm this individual {voiceAgent ? "voice-agent " : ""}call to the lead.</span></label><button type="submit" disabled={busy || !selected || !confirmed || !leadPhone || !data.outreach_allowed || (needsRep && !repPhone)}>{busy ? "Submitting…" : requestId ? "Check existing call request" : voiceAgent ? "Start voice-agent call" : data.records.length ? "Call again" : "Call lead"}</button></>}
        {!canCall && <p>Calling this lead requires the Dialer calling permission plus lead-view and lead-edit access. Ask an admin to grant any missing permission.</p>}
      </form>
    </>}
     {data && <div className="calls-history"><h4>Recent activity</h4>{data.records.length ? <div className="lead-table-wrap lead-browser call-table-wrap">
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
     </div> : <p className="ops-empty">No call activity recorded for this lead. Use Sync recent calls to import matching provider history.</p>}</div>}
      <small>History sync reads recent provider calls that match this lead number. AI insights load only when requested. Calls always require confirmation and pass consent checks.</small>
  </section>;
}
