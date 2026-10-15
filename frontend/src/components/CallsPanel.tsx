import { useEffect, useState, type FormEvent } from "react";
import { opsGet, opsSave } from "../operationsApi";
import { createClientId } from "../clientIds";
import { CallInsights, type CallInsightsData } from "./CallInsights";

const providerNames: Record<string, string> = { twilio: "Twilio", aircall: "Aircall", ringcentral: "RingCentral", vapi: "Vapi", retell: "Retell AI", cloudtalk: "CloudTalk" };
type CallRecord = { id: string; provider: string; external_id: string | null; status: string; direction: string; customer_phone: string; started_at: string | null; created_at: string; duration_seconds: number | null; transcript: string | null; recording_url: string | null; message: string | null; insights: CallInsightsData };
type Activity = { connections: { id: string; provider: string; account_name: string }[]; records: CallRecord[]; outreach_allowed: boolean; outreach_reason: string };

export function CallsPanel({ leadId, leadPhone, canCall, canEdit }: { leadId: string; leadPhone?: string | null; canCall: boolean; canEdit: boolean }) {
  const [data, setData] = useState<Activity | null>(null);
  const [connectionId, setConnectionId] = useState("");
  const [repPhone, setRepPhone] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [requestId, setRequestId] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
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
  async function refresh() { setData(await opsGet<Activity>(`/v1/calling/leads/${leadId}`)); }
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
      // A completed receipt permits a new explicitly confirmed request. Unknown
      // network outcomes keep their ID so a retry cannot call the lead twice.
      setRequestId("");
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
  return <section className="ops-panel calls-panel" aria-labelledby="calls-heading">
    <div className="ops-toolbar"><div><span className="eyebrow">Calling & voice agents</span><h3 id="calls-heading">Calls & voice activity</h3><p>{leadPhone ? `Lead number: ${leadPhone}` : "Add an international phone number to this lead before calling."}</p></div>
      {canEdit && data && data.connections.length > 0 && <button type="button" className="secondary-button" disabled={!connectionId || !leadPhone || busy} onClick={() => void sync()}>{busy ? "Working…" : "Sync recent calls"}</button>}
    </div>
    {error && <p className="notice" role="alert">{error}</p>}
    {notice && <p className="notice" role="status">{notice}</p>}
    {!data && !error && <p role="status">Loading call activity…</p>}
    {data && data.connections.length === 0 && <div className="ops-empty"><strong>No calling provider connected</strong><p>Connect and verify Twilio, Aircall, RingCentral, Vapi or Retell AI in Integrations to call from this lead.</p><a href="?view=integrations">Manage calling integrations →</a></div>}
    {data && data.connections.length > 0 && <>
      {!data.outreach_allowed && <p className="notice" role="status">{data.outreach_reason}</p>}
      <form onSubmit={(event) => void start(event)} className="calls-form">
        <div className="ops-form-grid">
          <label>Calling provider<select value={connectionId} disabled={busy || Boolean(requestId)} onChange={(event) => { setConnectionId(event.target.value); setConfirmed(false); }}><option value="">Choose a verified provider</option>{data.connections.map((item) => <option value={item.id} key={item.id}>{providerNames[item.provider]} · {item.account_name}</option>)}</select></label>
          {selected && ["twilio", "ringcentral"].includes(selected.provider) && <label>Your rep number{needsRep ? " (required)" : " (optional override)"}<input type="tel" placeholder="+14155550123" value={repPhone} required={needsRep} disabled={busy || Boolean(requestId)} onChange={(event) => { setRepPhone(event.target.value); setConfirmed(false); }} autoComplete="tel" /><small>{needsRep ? "Twilio rings you first, then connects the lead." : "Leave blank to ring the rep number saved in Integrations."}</small></label>}
        </div>
         {selected && <p>{voiceAgent ? "This uses the assistant and phone number configured in your provider account. The saved voice agent talks to the lead." : selected.provider === "aircall" ? "The selected Aircall user must be signed in and available in Aircall Workspace." : selected.provider === "cloudtalk" ? "CloudTalk rings the configured agent first, then connects the lead once the agent answers." : "Answer your rep phone to connect to the lead."}</p>}
        {canCall && <><label className="ops-check"><input type="checkbox" checked={confirmed} disabled={busy || !data.outreach_allowed || !leadPhone || !selected} onChange={(event) => setConfirmed(event.target.checked)} /><span>I confirm this individual {voiceAgent ? "voice-agent " : ""}call to the lead.</span></label><button type="submit" disabled={busy || !selected || !confirmed || !leadPhone || !data.outreach_allowed || (needsRep && !repPhone)}>{busy ? "Submitting…" : requestId ? "Check existing call request" : voiceAgent ? "Start voice-agent call" : "Call lead"}</button></>}
        {!canCall && <p>Your workspace role can view call history. Calling requires lead editing and agent action permissions.</p>}
      </form>
    </>}
     {data && <div className="calls-history"><h4>Recent activity</h4>{data.records.length ? data.records.map((call) => <article className="call-record" key={call.id}><div className="ops-toolbar"><div><strong>{providerNames[call.provider] || call.provider}</strong><p>{new Date(call.started_at || call.created_at).toLocaleString()} · {call.direction.replaceAll("_", " ")}{call.duration_seconds !== null ? ` · ${call.duration_seconds}s` : ""}</p></div><span className="call-status">{call.status.replaceAll("_", " ")}</span></div>{call.message && <p>{call.message}</p>}{call.provider === "cloudtalk" && <CallInsights insights={call.insights ?? null} />}{call.recording_url && <a href={call.recording_url} target="_blank" rel="noopener noreferrer">Open provider recording ↗</a>}{call.transcript && <details><summary>View transcript</summary><pre>{call.transcript}</pre></details>}{!call.transcript && !call.recording_url && <small>Transcripts and recordings appear only when provided by the connected service.</small>}</article>) : <p className="ops-empty">No call activity recorded for this lead.</p>}</div>}
    <small>History sync reads recent provider calls that match this lead number. Calls always require confirmation and pass consent checks.</small>
  </section>;
}
