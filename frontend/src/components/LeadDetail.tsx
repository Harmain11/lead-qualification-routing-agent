import { useEffect, useState, type FormEvent } from "react";

import { approveAndSendLeadFollowUp, deleteLead, generateLeadFollowUp, getLead, getLeadFollowUp, getLeadMomentum, getLeadPipelineHistory, listIntegrations, sendLeadToCrm, syncLeadGmailConversation, updateLeadFollowUp, updateLeadProgress, updateLeadTitle } from "../api";
import type { CrmSync, FollowUpEmail, Lead, LeadMomentum } from "../types";
import { LeadMeetingPanel } from "./LeadMeetingPanel";

const title = (value: string) => value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
const crmProviderLabel = (provider: string) => provider === "hubspot" ? "HubSpot" : title(provider);

export function LeadLifecycle({ lead }: { lead: Lead }) {
  const stages = [
    ["Intake", true],
    ["Normalize", true],
    ["Analyze", Boolean(lead.analysis)],
    ["Qualify", Boolean(lead.qualification)],
    ["Route", Boolean(lead.routing)],
    ["Notify", Boolean(lead.routing?.notification_state)],
    ["Email", lead.confirmation_email?.status === "sent"],
    ["CRM", Boolean(lead.crm_sync)],
  ] as const;
  return <div className="detail-lifecycle" aria-label="Lead lifecycle">{stages.map(([stage, complete], index) => <div className={complete ? "complete" : "waiting"} key={stage}><i>{complete ? "✓" : index + 1}</i><span>{stage}</span></div>)}</div>;
}

function LeadMomentumPanel({ lead }: { lead: Lead }) {
  const [momentum, setMomentum] = useState<LeadMomentum | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    getLeadMomentum(lead.id, controller.signal)
      .then((value) => { if (!controller.signal.aborted) setMomentum(value); })
      .catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load momentum evidence."); });
    return () => controller.abort();
  }, [lead.id]);
  if (error) return <section className="story-section momentum-panel"><div className="story-index">∆</div><div><span className="eyebrow">Lead momentum</span><h3>Evidence is temporarily unavailable</h3><p className="story-summary">{error}</p></div></section>;
  if (!momentum) return <section className="story-section momentum-panel"><div className="story-index">∆</div><div><span className="eyebrow">Lead momentum</span><h3>Loading evidence trail…</h3></div></section>;
  const delta = momentum.score_delta === null ? "First assessment" : `${momentum.score_delta >= 0 ? "+" : ""}${momentum.score_delta} since last re-check`;
  return <section className="story-section momentum-panel"><div className="story-index">∆</div><div>
    <span className="eyebrow">Lead momentum · evidence first</span><h3>What changed, and what should happen next</h3>
    <div className="momentum-summary">
      <div className={`momentum-state state-${momentum.state}`}><span>Current temperature</span><b>{title(momentum.state)}</b><small>{title(momentum.classification)} · {momentum.score}/100</small></div>
      <div><span>Score movement</span><b>{delta}</b><small>{momentum.previous_score === null ? "No previous score has been recorded." : `${momentum.previous_score}/100 → ${momentum.score}/100`}</small></div>
      <div><span>Evidence freshness</span><b>{momentum.freshness_hours < 1 ? "Just updated" : `${momentum.freshness_hours}h ago`}</b><small>{momentum.observed_signals} verified signal{momentum.observed_signals === 1 ? "" : "s"} considered</small></div>
    </div>
    <div className="next-human-action"><span>Next best human action</span><b>{momentum.next_human_action}</b><small>This is a recommendation; Corral will not send or reroute anything automatically.</small></div>
    <div className="momentum-columns">
      <div><h4>Evidence ledger</h4>{momentum.evidence.length ? <div className="momentum-evidence">{momentum.evidence.map((item, index) => <article key={`${item.source}-${item.observed_at}-${index}`}><span>{item.source}</span><p>{item.excerpt}</p><time>{item.observed_at ? new Date(item.observed_at).toLocaleString() : "Original assessment"}</time></article>)}</div> : <p className="story-summary">No scored evidence is available yet. Add verified progress when new information arrives.</p>}</div>
      <div><h4>Delivery ledger</h4><div className="momentum-delivery">{momentum.delivery.map((item) => <article className={item.needs_attention ? "needs-attention" : ""} key={item.destination}><span>{item.destination}</span><b>{title(item.status)}</b><p>{item.detail}</p>{item.attempted_at && <time>{new Date(item.attempted_at).toLocaleString()}</time>}</article>)}</div></div>
    </div>
  </div></section>;
}

export function LeadDetail({ lead, canCrmSync = false, canEdit = false, onUpdated, onDeleted }: { lead: Lead; canCrmSync?: boolean; canEdit?: boolean; onUpdated?: (lead: Lead) => void; onDeleted?: (leadId: string) => void }) {
  const [sending, setSending] = useState(false);
  const [sendNotice, setSendNotice] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState(false);
  const [jobTitle, setJobTitle] = useState(lead.job_title || "");
  const [savingTitle, setSavingTitle] = useState(false);
  const [editNotice, setEditNotice] = useState<string | null>(null);
  const [crmResult, setCrmResult] = useState<CrmSync | null>(null);
  const [crmProviders, setCrmProviders] = useState<string[]>([]);
  const [selectedProvider, setSelectedProvider] = useState(lead.crm_sync?.provider || "");
  const [meetingRefreshToken, setMeetingRefreshToken] = useState(0);
  const [activityRefreshToken, setActivityRefreshToken] = useState(0);
  useEffect(() => {
    let active = true;
    listIntegrations().then((cards) => {
      if (!active) return;
      const providers = cards.filter((card) =>
        card.category.toLowerCase() === "crm" && card.directionality.manual_outbound &&
        card.connection?.is_active && card.connection.verification_status === "verified"
      ).map((card) => card.provider);
      setCrmProviders(providers);
      setSelectedProvider((current) =>
        providers.includes(lead.crm_sync?.provider || "") ? lead.crm_sync!.provider :
        providers.includes(current) ? current : providers.length === 1 ? providers[0] : ""
      );
    }).catch(() => { if (active) setCrmProviders([]); });
    return () => { active = false; };
  }, [lead.id, lead.crm_sync?.provider]);
  const analysis = lead.analysis;
  const qualification = lead.qualification;
  const route = lead.routing;
  const crm = crmResult?.lead_id === lead.id ? crmResult : lead.crm_sync;
  const icpRows = analysis ? [
    ["Industry", analysis.icp_evaluation.industry_fit],
    ["Company size", analysis.icp_evaluation.company_size_fit],
    ["Geography", analysis.icp_evaluation.geography_fit],
    ["Buyer role", analysis.icp_evaluation.buyer_role_fit],
    ["Budget", analysis.icp_evaluation.budget_fit],
    ["Pain relevance", analysis.icp_evaluation.pain_point_relevance],
  ] as const : [];

  return <article className="lead-story">
    <header className="story-head"><div><span className="eyebrow">Complete decision trail</span><h2>{lead.company_name}</h2><p>{lead.full_name} · {lead.job_title || "Role not supplied"}</p>{canEdit && (editingTitle ? <form onSubmit={async (event) => { event.preventDefault(); setSavingTitle(true); setEditNotice(null); try { const updated = await updateLeadTitle(lead.id, jobTitle); onUpdated?.(updated); setEditingTitle(false); setEditNotice("Title saved locally. No CRM was changed."); } catch (error) { setEditNotice(error instanceof Error ? error.message : "Unable to save title"); } finally { setSavingTitle(false); } }}><label>Job title <input aria-label="Job title" maxLength={255} required value={jobTitle} onChange={(event) => setJobTitle(event.target.value)} /></label><button className="secondary-button" type="submit" disabled={savingTitle}>{savingTitle ? "Saving…" : "Save title"}</button><button className="button-quiet" type="button" onClick={() => { setJobTitle(lead.job_title || ""); setEditingTitle(false); setEditNotice(null); }}>Cancel</button></form> : <button className="button-quiet" onClick={() => { setJobTitle(lead.job_title || ""); setEditingTitle(true); setEditNotice(null); }}>Edit title</button>)}{editNotice && <div className="notice" role="status">{editNotice}</div>}</div><div className="story-head-side">{qualification && <div className={`score-orb band-${qualification.classification.toLowerCase().replaceAll("_", "-")}`}><strong>{qualification.total_score}</strong><span>{title(qualification.classification)}</span></div>}{canEdit && <button className="danger-button" type="button" onClick={async () => { if (!window.confirm("Delete this lead from the workspace? A later matching inquiry can be imported again.")) return; try { await deleteLead(lead.id); onDeleted?.(lead.id); } catch (error) { setEditNotice(error instanceof Error ? error.message : "Unable to delete lead"); } }}>Delete lead</button>}</div></header>
    <LeadLifecycle lead={lead} />
    <LeadMomentumPanel lead={lead} />
    <LeadProgressEditor
      lead={lead}
      canEdit={canEdit}
      refreshToken={activityRefreshToken}
      onConversationSynced={() => {
        setActivityRefreshToken((revision) => revision + 1);
        setMeetingRefreshToken((revision) => revision + 1);
      }}
    />
    <LeadMeetingPanel
      lead={lead}
      canEdit={canEdit}
      refreshToken={meetingRefreshToken}
      onScheduled={() => setActivityRefreshToken((revision) => revision + 1)}
    />
    {route && <section className="story-section"><div className="story-index">→</div><div><span className="eyebrow">Follow-up strategy</span><h3>How to move this lead forward</h3><div className="follow-up-plan"><div className="follow-up-goal"><span>Conversion goal</span>{route.action_plan.conversion_goal || route.action_plan.next_step_recommendation}</div><h4>Recommended sequence</h4><ol>{(route.action_plan.follow_up_strategy?.length ? route.action_plan.follow_up_strategy : [route.action_plan.immediate_action, route.action_plan.next_step_recommendation]).map((step, index) => <li key={`${index}-${step}`}>{step}</li>)}</ol>{route.manual_review && <p className="attention-callout">Validate the analysis before contacting this lead.</p>}</div></div></section>}
    <FollowUpComposer lead={lead} canEdit={canEdit} />
    <section className="story-section"><div className="story-index">01</div><div><span className="eyebrow">Source / original inquiry</span><h3>{title(lead.source)} signal</h3><blockquote>{lead.message}</blockquote><div className="fact-grid"><Fact label="Received" value={new Date(lead.received_at).toLocaleString()} /><Fact label="Source detail" value={lead.source_detail || "Unspecified"} /><Fact label="External ID" value={lead.external_id || "Not supplied"} /></div></div></section>
    <section className="story-section"><div className="story-index">02</div><div><span className="eyebrow">Normalized lead</span><h3>Canonical commercial profile</h3><div className="fact-grid"><Fact label="Contact" value={`${lead.full_name} · ${lead.email}`} /><Fact label="Company" value={lead.company_name} /><Fact label="Industry" value={lead.industry || "Unknown"} /><Fact label="Geography" value={lead.geography || "Unknown"} /><Fact label="Team size" value={lead.employee_count ? `${lead.employee_count} people` : "Unknown"} /><Fact label="Requested service" value={lead.product_or_service} /></div></div></section>
    <section className="story-section"><div className="story-index">03</div><div><span className="eyebrow">AI context / intent</span><h3>{analysis ? `${title(analysis.intent_level)} intent · ${Math.round(analysis.overall_confidence * 100)}% confidence` : "Analysis unavailable"}</h3>{analysis && <><p className="story-summary">{analysis.summary.need} {analysis.summary.pain_point}</p><div className="signal-strip"><Signal name="Urgency" value={analysis.context.urgency.value} kind={analysis.context.urgency.information_kind} /><Signal name="Timeline" value={analysis.context.explicit_timeline.value} kind={analysis.context.explicit_timeline.information_kind} /><Signal name="Budget" value={analysis.context.budget_information.value} kind={analysis.context.budget_information.information_kind} /><Signal name="Buying stage" value={analysis.context.buying_stage.value} kind={analysis.context.buying_stage.information_kind} /></div>{analysis.requires_manual_review && <div className="attention-callout"><b>Human review required</b>{analysis.error_message || "Analysis confidence needs validation."}</div>}</>}</div></section>
    <section className="story-section"><div className="story-index">04</div><div><span className="eyebrow">ICP evaluation</span><h3>{analysis?.icp_evaluation.profile_name || "Profile unavailable"}</h3><div className="icp-matrix">{icpRows.map(([name, criterion]) => <div key={name}><i className={criterion.status} /><span>{name}</span><b>{title(criterion.status)}</b><small>{criterion.rationale}</small></div>)}</div></div></section>
    <section className="story-section"><div className="story-index">05</div><div><span className="eyebrow">Qualification</span><h3>{qualification ? `${title(qualification.classification)} · ${qualification.total_score}/100` : "Not qualified"}</h3>{qualification && <><div className="score-contract">{Object.entries(qualification.component_scores).map(([component, score]) => { const maximum = component === "icp_fit" ? 40 : component === "intent_urgency" ? 35 : 25; return <div key={component}><span>{title(component)}</span><b>{score}<small>/{maximum}</small></b><i><span style={{ transform: `scaleX(${score / maximum})` }} /></i></div>; })}</div><ul className="evidence-list">{qualification.rationale.map((reason) => <li key={reason}>{reason}</li>)}</ul></>}</div></section>
    <section className="story-section"><div className="story-index">06</div><div><span className="eyebrow">Routing / action plan</span><h3>{route ? `${title(route.assigned_queue)}${route.assigned_rep_name ? ` · ${route.assigned_rep_name}` : ""}` : "Routing pending"}</h3>{route && <><p className="story-summary">{route.routing_reason}</p><div className="action-command"><span>Recommended immediate action</span><b>{route.action_plan.immediate_action}</b><p>{route.action_plan.next_step_recommendation}</p></div><div className="split-list"><div><span className="eyebrow">Talking points</span>{route.action_plan.talking_points.map((item) => <p key={item}>→ {item}</p>)}</div><div><span className="eyebrow">Open questions</span>{route.action_plan.unanswered_questions.length ? route.action_plan.unanswered_questions.map((item) => <p key={item}>→ {item}</p>) : <p>No open qualification questions.</p>}</div></div></>}</div></section>
    <section className="story-section story-delivery"><div className="story-index">07</div><div><span className="eyebrow">Notification / email / CRM delivery</span><h3>External system state</h3><div className="delivery-grid"><div><span>Notification</span><b>{route ? title(route.notification_state) : "Pending"}</b><small>{route?.notifications[0]?.provider || "No provider event"}</small></div><div><span>Confirmation email</span><b>{lead.confirmation_email ? title(lead.confirmation_email.status) : "Not attempted"}</b><small>{lead.confirmation_email?.error_message || lead.confirmation_email?.recipient || "Requires a verified Gmail connection"}</small></div><div><span>CRM</span><b>{crm ? `${crmProviderLabel(crm.provider)} · ${title(crm.status)}` : "Not synchronized"}</b><small>{crm?.provider_entity_ids.contact_id || "No provider record ID"}</small></div></div>{canCrmSync && <div className="crm-action-controls">{crmProviders.length > 1 && <label>CRM provider <select aria-label="CRM provider" value={selectedProvider} onChange={(event) => setSelectedProvider(event.target.value)}><option value="">Select a CRM</option>{crmProviders.map((provider) => <option key={provider} value={provider}>{crmProviderLabel(provider)}</option>)}</select></label>}<button className="primary-button" disabled={sending || !selectedProvider} onClick={async () => { setSending(true); setSendNotice(null); try { const result = await sendLeadToCrm(lead.id, selectedProvider); setCrmResult(result); setSendNotice(result.status === "synced" ? `${crmProviderLabel(selectedProvider)} synchronized by your explicit request.` : `${crmProviderLabel(selectedProvider)} write ${title(result.status)}.`); try { onUpdated?.(await getLead(lead.id)); } catch { setSendNotice(`${crmProviderLabel(selectedProvider)} write ${title(result.status)}. Refresh lead details to see the latest state.`); } } catch (error) { setSendNotice(error instanceof Error ? error.message : "CRM write failed"); } finally { setSending(false); } }}>{sending ? "Sending…" : crm?.provider === selectedProvider && crm.status === "failed" ? `Retry ${crmProviderLabel(selectedProvider)}` : crm?.provider === selectedProvider && crm.status === "synced" ? `Update ${crmProviderLabel(selectedProvider)}` : `Send to ${crmProviderLabel(selectedProvider || "CRM")}`}</button></div>}{sendNotice && <div className="notice" role="status">{sendNotice}</div>}{crm?.last_error && <div className="attention-callout"><b>{crm.error_category || "CRM delivery issue"}</b>{crm.last_error}</div>}</div></section>
  </article>;
}

function LeadProgressEditor({
  lead,
  canEdit,
  refreshToken,
  onConversationSynced,
}: {
  lead: Lead;
  canEdit: boolean;
  refreshToken: number;
  onConversationSynced: () => void;
}) {
  const [events, setEvents] = useState<Awaited<ReturnType<typeof getLeadPipelineHistory>>>([]);
  const [stage, setStage] = useState("contacted");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [syncBusy, setSyncBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    getLeadPipelineHistory(lead.id, controller.signal).then((rows) => {
      if (controller.signal.aborted) return;
      setEvents(rows);
      const latest = [...rows].reverse().find((event) => event.stage === "manual_progress");
      if (latest) {
        const savedStage = latest.details.stage;
        if (typeof savedStage === "string") setStage(savedStage);
      }
    }).catch(() => undefined);
    return () => controller.abort();
  }, [lead.id, refreshToken]);
  const progressEvents = events.filter((event) => event.stage === "manual_progress").reverse();
  const emailEvents = events.filter((event) => event.stage === "gmail_message").reverse();
  const meetingEvents = events.filter((event) => event.stage === "meeting_scheduled").reverse();
  const syncConversation = async () => {
    setSyncBusy(true); setNotice(null);
    try {
      const result = await syncLeadGmailConversation(lead.id);
      setEvents(await getLeadPipelineHistory(lead.id));
      onConversationSynced();
      const summary = result.messages_added
        ? `Synced ${result.messages_added} new Gmail messages across ${result.threads_checked} threads. AI re-evaluated this lead as ${title(result.classification)} (${result.score}/100).${result.truncated ? " More than 200 threads were found; sync was capped for safety." : ""}`
        : `No new Gmail messages found across ${result.threads_checked} threads. Current assessment: ${title(result.classification)} (${result.score}/100).`;
      const inaccessible = result.threads_unreadable
        ? ` Google denied access to ${result.threads_unreadable} Gmail thread${result.threads_unreadable === 1 ? "" : "s"}; verify the connected mailbox can open it.`
        : "";
      setNotice(`${summary}${inaccessible}`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Unable to sync Gmail conversation");
    } finally { setSyncBusy(false); }
  };
  const save = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setNotice(null);
    try {
      const result = await updateLeadProgress(lead.id, { stage, note });
      setNote("");
      setNotice(`Progress saved. AI re-evaluated this lead as ${title(result.classification)} (${result.score}/100).`);
      setEvents(await getLeadPipelineHistory(lead.id));
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Unable to update lead progress");
    } finally { setBusy(false); }
  };
  return <section className="story-section lead-progress"><div className="story-index">↗</div><div>
    <span className="eyebrow">Sales progress · AI re-evaluation</span><h3>Keep this lead current</h3>
    <p className="story-summary">Record what happened after contact. The original inquiry stays unchanged; AI uses your verified updates to re-check lead temperature and routing.</p>
    {canEdit && <div className="lead-progress-sync"><button className="secondary-button" type="button" disabled={syncBusy || busy} onClick={() => void syncConversation()}>{syncBusy ? "Syncing Gmail thread…" : "Sync Gmail conversation & re-check"}</button><small>Read-only sync for messages exchanged with this lead. Imported message text is used as qualification context.</small></div>}
    {canEdit && <form className="lead-progress-form" onSubmit={(event) => void save(event)}>
      <label className="follow-up-field">Current stage<select value={stage} onChange={(event) => setStage(event.target.value)}>
        <option value="contacted">Contacted</option><option value="responded">Responded</option><option value="meeting_scheduled">Meeting scheduled</option><option value="proposal_sent">Proposal sent</option><option value="converted">Converted</option><option value="lost">Closed / lost</option>
      </select></label>
      <label className="follow-up-field">Verified update<textarea value={note} onChange={(event) => setNote(event.target.value)} rows={3} minLength={3} maxLength={2000} placeholder="Example: Replied asking for a proposal by Friday; timeline is now this quarter." required /></label>
      <button className="secondary-button" type="submit" disabled={busy || note.trim().length < 3}>{busy ? "Saving & re-evaluating…" : "Save progress & re-check lead"}</button>
    </form>}
    {progressEvents.length > 0 && <div className="lead-progress-history"><h4>Progress history</h4>{progressEvents.slice(0, 5).map((event) => <article key={event.id}><b>{title(String(event.details.stage || event.status))}</b><time>{new Date(event.created_at).toLocaleString()}</time><p>{String(event.details.note || "")}</p><small>AI assessment: {title(String((event.details.qualification as { classification?: string } | undefined)?.classification || "pending"))} · {String((event.details.qualification as { score?: number } | undefined)?.score ?? "—")}/100</small></article>)}</div>}
    {emailEvents.length > 0 && <div className="lead-progress-history"><h4>Gmail conversation history</h4>{emailEvents.slice(0, 10).map((event) => <article key={event.id}><b>{event.details.direction === "outbound" ? "You sent" : "Lead sent"}: {String(event.details.subject || "(no subject)")}</b><time>{new Date(event.created_at).toLocaleString()}</time><p>{String(event.details.body || "")}</p><small>{String(event.details.sender || "")} · {String(event.details.recipients || "")}</small></article>)}</div>}
    {meetingEvents.length > 0 && <div className="lead-progress-history"><h4>Scheduled meetings</h4>{meetingEvents.slice(0, 10).map((event) => {
      const start = String(event.details.start_at || "");
      const provider = String(event.details.provider || "");
      const eventUrl = event.details.event_url;
      return <article key={event.id}><b>{String(event.details.title || "Meeting")} · {title(provider)}</b><time>{start ? new Date(start).toLocaleString() : new Date(event.created_at).toLocaleString()}</time><p>Scheduled from the Gmail conversation. The connected workspace account is the host.</p>{typeof eventUrl === "string" && eventUrl && <a href={eventUrl} target="_blank" rel="noreferrer">Open event</a>}</article>;
    })}</div>}
    {notice && <div className="notice" role="status">{notice}</div>}
  </div></section>;
}

function FollowUpComposer({ lead, canEdit }: { lead: Lead; canEdit: boolean }) {
  const [draft, setDraft] = useState<FollowUpEmail | null>(null);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    getLeadFollowUp(lead.id, controller.signal).then((value) => {
      if (controller.signal.aborted) return;
      setDraft(value); setSubject(value?.subject || ""); setBody(value?.body || "");
    }).catch((error) => { if (!controller.signal.aborted) setNotice(error instanceof Error ? error.message : "Unable to load follow-up draft"); });
    return () => controller.abort();
  }, [lead.id]);
  const dirty = Boolean(draft && (subject !== draft.subject || body !== draft.body));
  const classification = lead.qualification?.classification.toLowerCase();
  const disqualified = Boolean(lead.qualification?.hard_disqualified) || ["disqualify", "disqualified", "hard_disqualified"].includes(classification || "");
  const requiresReview = Boolean(lead.routing?.manual_review || lead.analysis?.requires_manual_review);
  const blockedReason = disqualified
    ? "This lead is classified as disqualified. Review the evidence and update its qualification before creating outreach."
    : requiresReview
      ? "This lead needs human review first. Add verified context in Sales progress and re-check its qualification before creating outreach."
      : null;
  const run = async (action: () => Promise<FollowUpEmail>, success: string) => {
    setBusy(true); setNotice(null);
    try {
      const result = await action(); setDraft(result); setSubject(result.subject); setBody(result.body || ""); setNotice(success);
    } catch (error) { setNotice(error instanceof Error ? error.message : "Follow-up action failed"); }
    finally { setBusy(false); }
  };
  const statusLabel = (value: string) => value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
  return <section className="story-section follow-up-composer"><div className="story-index">✉</div><div>
    <span className="eyebrow">AI follow-up · human approved</span><h3>Draft a personal next step</h3>
    {!draft && <><p className="story-summary">Create and edit a lead-specific follow-up here. Nothing is sent until you review it and click Send follow-up; Corral uses the connected Gmail account.</p>{blockedReason && <div className="attention-callout follow-up-blocked"><b>{disqualified ? "Outreach paused · disqualified" : "Outreach paused · review required"}</b>{blockedReason}</div>}{canEdit && <button className="secondary-button" disabled={busy || Boolean(blockedReason)} onClick={() => void run(() => generateLeadFollowUp(lead.id), "Draft created. Review and edit it, then click Send follow-up to send through connected Gmail.")}>{busy ? "Preparing draft…" : "Generate follow-up draft"}</button>}</>}
    {draft && <>
      <div className={`follow-up-status status-${draft.status}`}><span>{statusLabel(draft.status)}</span>{draft.generation_method && <small>{draft.generation_method === "openai" ? "AI-generated · review required" : "Grounded template"}</small>}</div>
      {draft.status === "draft" ? <>
        <p className="follow-up-recipient">To <b>{draft.recipient}</b></p>
        <label className="follow-up-field">Subject<input value={subject} maxLength={180} onChange={(event) => setSubject(event.target.value)} /></label>
        <label className="follow-up-field">Message<textarea value={body} rows={8} maxLength={5000} onChange={(event) => setBody(event.target.value)} /></label>
        {draft.rationale && <p className="follow-up-rationale"><b>Why this draft:</b> {draft.rationale}</p>}
        {draft.generation_note && <p className="follow-up-rationale">{draft.generation_note}</p>}
        {canEdit && <div className="follow-up-actions"><button className="secondary-button" disabled={busy || !dirty || !subject.trim() || body.trim().length < 10} onClick={() => void run(() => updateLeadFollowUp(lead.id, draft.id, { subject, body }), "Draft saved.")}>{busy ? "Saving…" : "Save edits"}</button><button className="primary-button" disabled={busy || dirty || !subject.trim() || body.trim().length < 10} onClick={() => { if (window.confirm(`Send this edited email to ${draft.recipient} from the connected Gmail account?`)) void run(() => approveAndSendLeadFollowUp(lead.id, draft.id), "Follow-up sent from the connected Gmail account."); }}>Send follow-up</button></div>}
      </> : <><p className="follow-up-recipient">To <b>{draft.recipient}</b></p><p className="follow-up-status-copy">{draft.status === "sent" ? `Sent ${draft.sent_at ? new Date(draft.sent_at).toLocaleString() : "successfully"}.` : draft.error_message || "Delivery status is being checked."}</p>{draft.status === "failed" && canEdit && <button className="secondary-button" disabled={busy} onClick={() => void run(() => generateLeadFollowUp(lead.id), "A new editable draft is ready for review.")}>Create a new draft</button>}</>}
    </>}
    {notice && <div className="notice" role="status">{notice}</div>}
  </div></section>;
}

function Fact({ label, value }: { label: string; value: string }) { return <div className="fact"><span>{label}</span><b>{value}</b></div>; }
function Signal({ name, value, kind }: { name: string; value: string | null; kind: string }) { return <div className={`signal ${kind}`}><span>{name} · {kind}</span><b>{value || "Unknown"}</b></div>; }
