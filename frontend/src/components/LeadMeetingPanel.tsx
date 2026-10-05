import { useEffect, useMemo, useRef, useState } from "react";
import { confirmLeadMeeting, getLeadMeetingRecommendations, syncLeadGmailConversation } from "../api";
import type { ConfirmMeetingResult, Lead, MeetingRecommendations, MeetingSuggestion } from "../types";

type Props = { lead: Lead; refreshToken?: number; canEdit: boolean; onScheduled?: () => void };
type LoadState = "loading" | "ready" | "error";

const providerName = (provider: string) => provider === "google_calendar" ? "Google Calendar" : provider === "calendly" ? "Calendly" : provider;
const slotKey = (slot: MeetingSuggestion) => [slot.provider, slot.connection_id, slot.source_message_id, slot.start_at, slot.end_at].join("|");

function dateLabel(value: string, timeZone: string, options: Intl.DateTimeFormatOptions) {
  try {
    return new Intl.DateTimeFormat(undefined, { ...options, timeZone }).format(new Date(value));
  } catch {
    return new Date(value).toLocaleString();
  }
}

function meetingWindow(startAt: string, endAt: string, timeZone: string) {
  return `${dateLabel(startAt, timeZone, { weekday: "short", month: "short", day: "numeric" })} · ${dateLabel(startAt, timeZone, { hour: "numeric", minute: "2-digit" })}–${dateLabel(endAt, timeZone, { hour: "numeric", minute: "2-digit" })}`;
}

const styles = `
.lmp { --lmp-ink:#18352c; --lmp-muted:#687c72; --lmp-line:#dce7df; --lmp-wash:#f3f7f2; --lmp-green:#286c53; --lmp-green-soft:#e8f2eb; --lmp-gold:#a36b24; color:var(--lmp-ink); border:1px solid var(--lmp-line); border-radius:16px; background:#fbfdfb; overflow:hidden; }
.lmp * { box-sizing:border-box; }
.lmp-head { display:flex; align-items:flex-start; justify-content:space-between; gap:18px; padding:22px 24px 18px; background:linear-gradient(115deg,#f4f8f3 0%,#fbfdfb 65%); border-bottom:1px solid var(--lmp-line); }
.lmp-head-main { display:flex; gap:13px; min-width:0; }
.lmp-mark { width:38px; height:38px; display:grid; place-items:center; flex:0 0 auto; border:1px solid #c8dccd; border-radius:11px; background:#eaf3eb; color:var(--lmp-green); font:700 11px/1 "Cascadia Mono","SFMono-Regular",monospace; letter-spacing:.04em; }
.lmp-eyebrow { display:block; margin:1px 0 5px; color:var(--lmp-green); font:600 9px/1.4 "Cascadia Mono","SFMono-Regular",monospace; letter-spacing:.14em; text-transform:uppercase; }
.lmp h3 { margin:0; color:var(--lmp-ink); font:600 19px/1.2 "Arial Nova","Segoe UI Variable",system-ui,sans-serif; letter-spacing:-.025em; }
.lmp-intro { margin:6px 0 0; color:var(--lmp-muted); font-size:11px; line-height:1.55; }
.lmp-refresh { display:inline-flex; align-items:center; gap:7px; min-height:34px; flex:0 0 auto; padding:0 11px; border:1px solid var(--lmp-line); border-radius:8px; background:#fff; color:#456255; font-size:10px; font-weight:650; transition:transform 160ms ease,border-color 160ms ease,background 160ms ease; }
.lmp-refresh:hover:not(:disabled) { transform:translateY(-1px); border-color:#a9c7b0; background:#f7faf7; }
.lmp-refresh:disabled { cursor:wait; opacity:.58; }
.lmp-refresh-mark { font-size:15px; line-height:0; }
.lmp-body { padding:19px 24px 23px; }
.lmp-state { display:flex; align-items:flex-start; gap:12px; padding:14px 15px; border:1px solid var(--lmp-line); border-radius:11px; background:white; }
.lmp-state-icon { width:25px; height:25px; display:grid; place-items:center; flex:0 0 auto; border-radius:8px; background:var(--lmp-wash); color:var(--lmp-green); font:700 11px "Cascadia Mono","SFMono-Regular",monospace; }
.lmp-state h4 { margin:1px 0 5px; color:var(--lmp-ink); font-size:12px; font-weight:700; }
.lmp-state p { margin:0; color:var(--lmp-muted); font-size:10px; line-height:1.65; }
.lmp-error { border-color:#ead4c9; background:#fffaf6; }
.lmp-error .lmp-state-icon { background:#f8ece4; color:#9b5438; }
.lmp-state-action { margin-top:10px; }
.lmp-link-button { padding:0; border:0; background:transparent; color:var(--lmp-green); font-size:10px; font-weight:700; text-decoration:underline; text-underline-offset:3px; }
.lmp-recovery-actions { display:flex; flex-wrap:wrap; align-items:center; gap:12px; margin-top:8px; }
.lmp-recovery-actions .lmp-link-button { display:inline-block; }
.lmp-skeleton { display:grid; gap:10px; }
.lmp-skeleton-line { height:11px; border-radius:8px; background:linear-gradient(90deg,#edf2ed 25%,#e3ebe4 42%,#edf2ed 65%); background-size:240% 100%; animation:lmp-shimmer 1.4s ease-in-out infinite; }
@keyframes lmp-shimmer { to { background-position:-120% 0; } }
.lmp-skeleton-line:nth-child(2) { width:76%; }.lmp-skeleton-line:nth-child(3) { width:52%; }
.lmp-context { margin-bottom:18px; padding:13px 15px; border-left:2px solid #8daf98; border-radius:0 9px 9px 0; background:#f3f7f2; }
.lmp-context-top { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:7px; margin-bottom:6px; }
.lmp-context-label { color:#5d7869; font:600 8px "Cascadia Mono","SFMono-Regular",monospace; letter-spacing:.11em; text-transform:uppercase; }
.lmp-subject { color:var(--lmp-ink); font-size:10px; font-weight:700; }
.lmp-excerpt { margin:0; color:#65786e; font-size:10px; line-height:1.65; }
.lmp-section-heading { display:flex; align-items:baseline; justify-content:space-between; gap:12px; margin:0 0 11px; }
.lmp-section-heading h4 { margin:0; color:var(--lmp-ink); font-size:12px; font-weight:700; }
.lmp-count { color:#7a8a80; font:9px "Cascadia Mono","SFMono-Regular",monospace; }
.lmp-groups { display:grid; gap:13px; }
.lmp-group { overflow:hidden; border:1px solid var(--lmp-line); border-radius:11px; background:#fff; }
.lmp-group-head { display:flex; align-items:center; gap:9px; padding:10px 12px; border-bottom:1px solid #edf1ed; background:#fafcf9; }
.lmp-provider-mark { width:24px; height:24px; display:grid; place-items:center; border:1px solid #dce7df; border-radius:7px; background:#fff; color:#426754; font:700 8px "Cascadia Mono","SFMono-Regular",monospace; }
.lmp-group-head div { min-width:0; }
.lmp-group-head b { display:block; overflow:hidden; color:var(--lmp-ink); font-size:10px; text-overflow:ellipsis; white-space:nowrap; }
.lmp-group-head small { display:block; margin-top:2px; color:#74857a; font-size:9px; }
.lmp-options { display:grid; gap:0; }
.lmp-option { position:relative; display:grid; grid-template-columns:18px minmax(0,1fr) auto; align-items:center; gap:10px; min-height:54px; padding:10px 12px; border-bottom:1px solid #edf1ed; cursor:pointer; transition:background 150ms ease; }
.lmp-option:last-child { border-bottom:0; }
.lmp-option:hover { background:#f7faf7; }
.lmp-option.is-selected { background:#edf5ee; }
.lmp-option input { width:14px; height:14px; margin:0; accent-color:var(--lmp-green); }
.lmp-option-time { color:#264638; font-size:11px; font-weight:700; }
.lmp-option-zone { display:block; margin-top:3px; color:#718278; font:8px "Cascadia Mono","SFMono-Regular",monospace; }
.lmp-option-duration { color:#75857b; font-size:9px; white-space:nowrap; }
.lmp-footnote { margin:12px 0 0; color:#718278; font-size:9px; line-height:1.55; }
.lmp-integrations { display:grid; gap:7px; margin-top:13px; }
.lmp-integration { display:flex; justify-content:space-between; align-items:center; gap:12px; padding:9px 11px; border:1px solid #e7ede7; border-radius:8px; background:#fff; color:#5c7165; font-size:9px; }
.lmp-integration b { color:#375545; font-size:9px; font-weight:650; }
.lmp-connection { display:inline-flex; align-items:center; gap:6px; color:#7b887e; font-size:8px; }
.lmp-connection::before { content:""; width:6px; height:6px; border-radius:50%; background:#c5cec6; }
.lmp-connection.connected { color:#347253; }.lmp-connection.connected::before { background:#4e9b6e; }
.lmp-errors { display:grid; gap:7px; margin-top:13px; }
.lmp-provider-error { padding:10px 12px; border:1px solid #eaded0; border-radius:9px; background:#fffaf4; color:#76563a; font-size:9px; line-height:1.5; }
.lmp-provider-error b { display:block; margin-bottom:3px; color:#875a30; font-size:9px; }
.lmp-controls { display:flex; align-items:center; justify-content:space-between; gap:14px; margin-top:16px; padding-top:14px; border-top:1px solid #e8eee8; }
.lmp-controls small { color:#75847b; font-size:9px; line-height:1.45; }
.lmp-primary { min-height:38px; flex:0 0 auto; padding:0 15px; border:1px solid #27664e; border-radius:8px; background:#286c53; color:#fff; font-size:10px; font-weight:700; transition:background 150ms ease,transform 150ms ease; }
.lmp-primary:hover:not(:disabled) { transform:translateY(-1px); background:#205840; }
.lmp-primary:disabled { cursor:not-allowed; opacity:.45; }
.lmp-review { padding:15px; border:1px solid #e3cfaa; border-radius:11px; background:#fffaf0; }
.lmp-review-title { display:flex; align-items:center; gap:8px; margin:0 0 9px; color:#765420; font-size:11px; font-weight:750; }
.lmp-review-badge { padding:4px 6px; border:1px solid #ead8b5; border-radius:5px; background:#fff5df; color:#89611f; font:700 7px "Cascadia Mono","SFMono-Regular",monospace; letter-spacing:.06em; text-transform:uppercase; }
.lmp-review p { margin:0; color:#705f45; font-size:10px; line-height:1.6; }
.lmp-review-slot { margin:11px 0; padding:10px 11px; border:1px solid #efe5d2; border-radius:8px; background:rgba(255,255,255,.68); color:#4b493e; font-size:10px; font-weight:700; line-height:1.5; }
.lmp-review-actions { display:flex; flex-wrap:wrap; justify-content:flex-end; gap:8px; margin-top:12px; }
.lmp-secondary { min-height:35px; padding:0 12px; border:1px solid #d9e2da; border-radius:8px; background:#fff; color:#526b5b; font-size:9px; font-weight:650; }
.lmp-secondary:hover:not(:disabled) { background:#f7faf7; }
.lmp-review .lmp-primary { min-height:35px; }
.lmp-outcome { display:flex; align-items:flex-start; gap:10px; margin-bottom:14px; padding:13px 14px; border:1px solid #bcd9c4; border-radius:10px; background:#edf7ef; }
.lmp-outcome-icon { width:24px; height:24px; display:grid; place-items:center; flex:0 0 auto; border-radius:50%; background:#d9eddd; color:#2d7049; font-size:12px; font-weight:800; }
.lmp-outcome h4 { margin:1px 0 4px; color:#28563b; font-size:11px; }
.lmp-outcome p { margin:0; color:#5b7562; font-size:9px; line-height:1.55; }
.lmp-outcome a { display:inline-block; margin-top:7px; color:#286c53; font-size:9px; font-weight:700; text-decoration:underline; text-underline-offset:3px; }
.lmp-outcome-failure { border-color:#ead4c9; background:#fff8f4; }
.lmp-outcome-failure .lmp-outcome-icon { background:#f5e2d8; color:#9b5438; }
.lmp-outcome-failure h4 { color:#854c38; }.lmp-outcome-failure p { color:#80685e; }
.lmp-sr-only { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; }
@media(max-width:640px) {
 .lmp-head { padding:17px 15px 15px; }.lmp-body { padding:15px; }.lmp h3 { font-size:17px; }
 .lmp-refresh { width:34px; padding:0; justify-content:center; font-size:0; }.lmp-refresh-mark { font-size:16px; }
 .lmp-option { grid-template-columns:18px minmax(0,1fr); }.lmp-option-duration { grid-column:2; margin-top:-3px; }
 .lmp-controls { align-items:stretch; flex-direction:column; }.lmp-controls .lmp-primary { width:100%; }
}
`;

export function LeadMeetingPanel({ lead, refreshToken, canEdit, onScheduled }: Props) {
  const [recommendations, setRecommendations] = useState<MeetingRecommendations | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [retryCount, setRetryCount] = useState(0);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [result, setResult] = useState<ConfirmMeetingResult | null>(null);
  const actionSequence = useRef(0);
  const currentLeadId = useRef(lead.id);
  const scanGmailOnNextLoad = useRef(false);
  currentLeadId.current = lead.id;

  useEffect(() => {
    const controller = new AbortController();
    const actionId = ++actionSequence.current;
    const shouldScanGmail = scanGmailOnNextLoad.current && canEdit;
    scanGmailOnNextLoad.current = false;
    setLoadState("loading");
    setLoadError(null);
    setRecommendations(null);
    setSelectedKey(null);
    setReviewing(false);
    setConfirming(false);
    setConfirmError(null);
    setResult(null);
    (async () => {
      if (shouldScanGmail) await syncLeadGmailConversation(lead.id);
      return getLeadMeetingRecommendations(lead.id, controller.signal);
    })()
      .then((value) => {
        if (controller.signal.aborted || actionSequence.current !== actionId) return;
        setRecommendations(value);
        setLoadState("ready");
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted || actionSequence.current !== actionId) return;
        setLoadError(reason instanceof Error ? reason.message : "Meeting recommendations could not be loaded.");
        setLoadState("error");
      });
    return () => {
      controller.abort();
      actionSequence.current += 1;
    };
  }, [lead.id, refreshToken, retryCount, canEdit]);

  const selectedSlot = useMemo(
    () => recommendations?.suggestions.find((slot) => slotKey(slot) === selectedKey) ?? null,
    [recommendations, selectedKey],
  );
  const groupedSuggestions = useMemo(() => {
    const groups = new Map<string, MeetingSuggestion[]>();
    for (const slot of recommendations?.suggestions ?? []) {
      const key = [slot.provider, slot.connection_id, slot.event_type_name || ""].join("|");
      groups.set(key, [...(groups.get(key) ?? []), slot]);
    }
    return [...groups.entries()];
  }, [recommendations]);
  const connectedCalendars = (recommendations?.integrations ?? []).filter((integration) => integration.connected);
  const hasCalendar = connectedCalendars.length > 0;
  const noThread = !recommendations?.source_message_id;
  const noMeetingDetected = Boolean(recommendations && !recommendations.detected && recommendations.source_message_id);

  const confirmSelectedSlot = async () => {
    if (!selectedSlot || !canEdit || confirming) return;
    const targetLeadId = lead.id;
    const actionId = ++actionSequence.current;
    setConfirming(true);
    setConfirmError(null);
    setResult(null);
    try {
      const confirmed = await confirmLeadMeeting(targetLeadId, selectedSlot);
      if (currentLeadId.current !== targetLeadId || actionSequence.current !== actionId) return;
      setResult(confirmed);
      onScheduled?.();
      setReviewing(false);
    } catch (reason: unknown) {
      if (currentLeadId.current !== targetLeadId || actionSequence.current !== actionId) return;
      setConfirmError(reason instanceof Error ? reason.message : "The meeting could not be scheduled. Your selected time is still available to retry.");
    } finally {
      if (currentLeadId.current === targetLeadId && actionSequence.current === actionId) setConfirming(false);
    }
  };

  const scheduleAnother = () => {
    setResult(null);
    setConfirmError(null);
    setReviewing(false);
  };

  const renderLoadError = () => (
    <div className="lmp-state lmp-error" role="alert">
      <span className="lmp-state-icon">!</span>
      <div><h4>Recommendations unavailable</h4><p>{loadError || "The meeting options could not be loaded."}</p><div className="lmp-state-action"><button className="lmp-link-button" type="button" onClick={() => setRetryCount((count) => count + 1)}>Try again</button></div></div>
    </div>
  );

  const retryAvailability = () => {
    scanGmailOnNextLoad.current = false;
    setRetryCount((count) => count + 1);
  };

  const renderSkeleton = () => (
    <div className="lmp-skeleton" aria-label="Loading meeting recommendations" role="status">
      <span className="lmp-sr-only">Loading meeting recommendations</span>
      <div className="lmp-skeleton-line" /><div className="lmp-skeleton-line" /><div className="lmp-skeleton-line" />
    </div>
  );

  return <section className="lmp" aria-labelledby={`meeting-title-${lead.id}`}>
    <style>{styles}</style>
    <header className="lmp-head">
      <div className="lmp-head-main">
        <span className="lmp-mark" aria-hidden="true">MTG</span>
        <div>
          <span className="lmp-eyebrow">Lead-confirmed · email scheduling</span>
          <h3 id={`meeting-title-${lead.id}`}>Schedule from the lead’s email</h3>
          <p className="lmp-intro">When a recent synced Gmail request has open times, Corral emails numbered options and books the lead’s clear numbered choice. You can still book manually below.</p>
        </div>
      </div>
      <button className="lmp-refresh" type="button" onClick={() => { scanGmailOnNextLoad.current = canEdit; setRetryCount((count) => count + 1); }} disabled={loadState === "loading" || confirming} aria-label={canEdit ? "Read latest Gmail conversation and refresh meeting recommendations" : "Refresh meeting recommendations"}>
        <span className="lmp-refresh-mark" aria-hidden="true">↻</span> {canEdit ? "Read Gmail & refresh" : "Refresh"}
      </button>
    </header>

    <div className="lmp-body">
      {loadState === "loading" && renderSkeleton()}
      {loadState === "error" && renderLoadError()}
      {loadState === "ready" && recommendations && <>
        {result && <div className="lmp-outcome" role="status">
          <span className="lmp-outcome-icon" aria-hidden="true">✓</span>
          <div><h4>Meeting scheduled</h4><p>{result.title} · {meetingWindow(result.start_at, result.end_at, result.time_zone)}. Hosted by {recommendations.integrations.find((integration) => integration.provider === result.provider && integration.connected)?.account_name || "the selected connected account"}.</p>
            {result.event_url && <a href={result.event_url} target="_blank" rel="noreferrer">Open meeting details</a>}
            <div><button className="lmp-link-button" type="button" onClick={scheduleAnother}>Review other times</button></div>
          </div>
        </div>}

        {!result && noThread && <div className="lmp-state">
          <span className="lmp-state-icon" aria-hidden="true">G</span>
          <div><h4>No Gmail conversation found</h4><p>{recommendations.message || "Sync the lead’s Gmail conversation to review meeting intent and available times."} Refresh these recommendations after the parent finishes a Gmail sync.</p></div>
        </div>}

        {!result && noMeetingDetected && <div className="lmp-state">
          <span className="lmp-state-icon" aria-hidden="true">—</span>
          <div><h4>No meeting request detected</h4><p>{recommendations.message || "The latest conversation does not contain a meeting request or proposed time."}</p></div>
        </div>}

        {!result && !noThread && !noMeetingDetected && recommendations.detected && <>
          {(recommendations.source_subject || recommendations.source_excerpt) && <div className="lmp-context">
            <div className="lmp-context-top"><span className="lmp-context-label">Conversation evidence</span>{recommendations.source_subject && <span className="lmp-subject">{recommendations.source_subject}</span>}</div>
            {recommendations.source_excerpt && <p className="lmp-excerpt">{recommendations.source_excerpt}</p>}
            {recommendations.message && <p className="lmp-excerpt">{recommendations.message}</p>}
          </div>}

          {!hasCalendar && <div className="lmp-state">
            <span className="lmp-state-icon" aria-hidden="true">!</span>
            <div><h4>No connected calendar account</h4><p>Connect a Google Calendar or Calendly account in Integrations settings to see bookable times. No event or message has been created.</p></div>
          </div>}

          {recommendations.suggestions.length > 0 && <>
            <div className="lmp-section-heading"><h4>Available times</h4><span className="lmp-count">{recommendations.suggestions.length} option{recommendations.suggestions.length === 1 ? "" : "s"}</span></div>
            <div className="lmp-groups">
              {groupedSuggestions.map(([groupKey, slots]) => {
                const first = slots[0];
                return <div className="lmp-group" key={groupKey}>
                  <div className="lmp-group-head"><span className="lmp-provider-mark" aria-hidden="true">{first.provider === "google_calendar" ? "G" : "C"}</span><div><b>{providerName(first.provider)} · {first.account_name}</b><small>{first.event_type_name || "Suggested meeting"}</small></div></div>
                  <div className="lmp-options" role="radiogroup" aria-label={`${providerName(first.provider)} times for ${first.account_name}`}>
                    {slots.map((slot) => {
                      const key = slotKey(slot);
                      const duration = Math.max(0, Math.round((new Date(slot.end_at).getTime() - new Date(slot.start_at).getTime()) / 60000));
                      return <label key={key} className={`lmp-option${selectedKey === key ? " is-selected" : ""}`}>
                        <input type="radio" name={`meeting-slot-${lead.id}`} value={key} checked={selectedKey === key} disabled={!canEdit || confirming || Boolean(result)} onChange={() => { setSelectedKey(key); setReviewing(false); setConfirmError(null); }} />
                        <span><span className="lmp-option-time">{meetingWindow(slot.start_at, slot.end_at, slot.time_zone)}</span><span className="lmp-option-zone">{slot.time_zone}</span></span>
                        <span className="lmp-option-duration">{duration} min</span>
                      </label>;
                    })}
                  </div>
                </div>;
              })}
            </div>
            <p className="lmp-footnote">Times are tied to the source email and selected workspace account. The connected account owner will host the meeting.</p>
          </>}

          {recommendations.suggestions.length === 0 && hasCalendar && <div className="lmp-state">
            <span className="lmp-state-icon" aria-hidden="true">…</span>
            <div><h4>No available times yet</h4><p>{recommendations.message || "The connected account did not return an available slot for this conversation."} Refresh to check again.</p></div>
          </div>}
        </>}

        {recommendations.integrations.length > 0 && <div className="lmp-integrations" aria-label="Calendar connection status">
          {recommendations.integrations.map((integration) => <div className="lmp-integration" key={`${integration.provider}-${integration.account_name || "account"}`}>
            <b>{providerName(integration.provider)}{integration.account_name ? ` · ${integration.account_name}` : ""}</b>
            <span className={`lmp-connection${integration.connected ? " connected" : ""}`}>{integration.connected ? "Connected" : integration.message || "Not connected"}</span>
          </div>)}
        </div>}

        {recommendations.provider_errors.length > 0 && <div className="lmp-errors" aria-label="Provider issues">
          {recommendations.provider_errors.map((error, index) => <div className="lmp-provider-error" key={`${error.provider}-${index}`}><b>{providerName(error.provider)}{error.retryable ? " · can retry" : ""}</b>{error.message}
            {error.code === "calendly_external_calendar_unavailable" && <div className="lmp-recovery-actions">
              <a className="lmp-link-button" href="https://calendly.com/help/how-to-reconnect-a-disconnected-calendar" target="_blank" rel="noreferrer">Open Calendly reconnect guide</a>
              <button className="lmp-link-button" type="button" onClick={retryAvailability} disabled={confirming}>Retry availability</button>
            </div>}
          </div>)}
        </div>}

        {!result && selectedSlot && !reviewing && !confirmError && <div className="lmp-controls">
            <small>{canEdit ? "Manual booking is available if the lead needs a different time." : "You can review suggested times; an editor can book manually."}</small>
          <button className="lmp-primary" type="button" disabled={!canEdit || confirming} onClick={() => setReviewing(true)}>Review selected time</button>
        </div>}
        {!result && selectedSlot && reviewing && <div className="lmp-review" role="group" aria-label="Confirm meeting booking">
          <div className="lmp-review-title"><span className="lmp-review-badge">Final review</span> Confirm this meeting?</div>
          <p>This action will add the meeting to the selected workspace account. Its connected account owner will be the host.</p>
          <div className="lmp-review-slot">{providerName(selectedSlot.provider)} · {selectedSlot.account_name}<br />{meetingWindow(selectedSlot.start_at, selectedSlot.end_at, selectedSlot.time_zone)} · {selectedSlot.time_zone}</div>
          <p><strong>Invite notice:</strong> Calendly or Google Calendar may send its normal invite notification when the meeting is added.</p>
          {confirmError && <div className="lmp-provider-error" role="alert" style={{ marginTop: 10 }}>{confirmError}</div>}
          <div className="lmp-review-actions">
            <button className="lmp-secondary" type="button" disabled={confirming} onClick={() => { setReviewing(false); setConfirmError(null); }}>Back to times</button>
            <button className="lmp-primary" type="button" disabled={!canEdit || confirming} onClick={() => void confirmSelectedSlot()}>{confirming ? "Adding meeting…" : "Confirm and add meeting"}</button>
          </div>
        </div>}
        {!result && confirmError && !reviewing && <div className="lmp-outcome lmp-outcome-failure" role="alert">
          <span className="lmp-outcome-icon" aria-hidden="true">!</span>
          <div><h4>Meeting was not scheduled</h4><p>{confirmError}</p><div><button className="lmp-link-button" type="button" onClick={() => { setConfirmError(null); setReviewing(true); }}>Review and try again</button></div></div>
        </div>}
      </>}
    </div>
  </section>;
}

export default LeadMeetingPanel;
