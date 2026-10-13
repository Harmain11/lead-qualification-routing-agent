import { CallsPanel } from "./CallsPanel";
import { CollaborationPanel } from "./CollaborationPanel";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { AiStageAssessment } from "./AiStageAssessment";

import { approveAndSendLeadFollowUp, deleteLead, generateLeadFollowUp, getLead, getLeadClosingOptions, getLeadFollowUp, getLeadFollowUpHistory, getLeadMomentum, getLeadPipelineHistory, listIntegrations, retryLeadCongratulations, sendLeadToCrm, syncLeadGmailConversation, updateLeadFollowUp, updateLeadProgress, updateLeadTitle } from "../api";
import type { CrmSync, FollowUpEmail, Lead, LeadMomentum, LeadOutcome } from "../types";
import { LeadMeetingPanel } from "./LeadMeetingPanel";
import "./leadOutcomes.css";
import "./leadDetailTabs.css";
import { leadDetailSections, leadSectionHref, type LeadDetailSection } from "../leadSections";

const title = (value: string) => value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
const crmProviderLabel = (provider: string) => provider === "hubspot" ? "HubSpot" : provider === "gohighlevel" ? "GoHighLevel" : title(provider);

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

type LeadDetailProps = {
  lead: Lead;
  canCrmSync?: boolean;
  canEdit?: boolean;
  canCall?: boolean;
  onUpdated?: (lead: Lead) => void;
  onDeleted?: (leadId: string) => void;
  activeSection: LeadDetailSection;
  onSectionChange: (section: LeadDetailSection) => void;
  isSectionPinned?: boolean;
  onTogglePin?: () => void;
};

export function LeadDetail({ lead, canCrmSync = false, canEdit = false, canCall = false, onUpdated, onDeleted, activeSection, onSectionChange, isSectionPinned = false, onTogglePin }: LeadDetailProps) {
  const [sending, setSending] = useState(false);
  const [sendNotice, setSendNotice] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState(false);
  const detailTabsRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const tabs = detailTabsRef.current;
    if (!tabs) return;
    const revealActiveTab = () => {
      const active = tabs.querySelector<HTMLElement>('[aria-selected="true"]');
      if (!active || tabs.scrollWidth <= tabs.clientWidth) return;
      const activeRect = active.getBoundingClientRect();
      const tabsRect = tabs.getBoundingClientRect();
      tabs.scrollLeft += activeRect.left - tabsRect.left - (tabs.clientWidth - activeRect.width) / 2;
    };
    revealActiveTab();
    const observer = new ResizeObserver(revealActiveTab);
    observer.observe(tabs);
    return () => observer.disconnect();
  }, [activeSection]);
  const [jobTitle, setJobTitle] = useState(lead.job_title || "");
  const [savingTitle, setSavingTitle] = useState(false);
  const [editNotice, setEditNotice] = useState<string | null>(null);
  const [crmResult, setCrmResult] = useState<CrmSync | null>(null);
  const [crmProviders, setCrmProviders] = useState<string[]>([]);
  const [selectedProvider, setSelectedProvider] = useState(lead.crm_sync?.provider || "");
  const [meetingRefreshToken, setMeetingRefreshToken] = useState(0);
  const [activityRefreshToken, setActivityRefreshToken] = useState(0);
  const updatedLeadCallback = useRef(onUpdated);
  useEffect(() => { updatedLeadCallback.current = onUpdated; }, [onUpdated]);
  useEffect(() => {
    if (!activityRefreshToken || !updatedLeadCallback.current) return;
    let active = true;
    getLead(lead.id).then((updated) => {
      if (active) updatedLeadCallback.current?.(updated);
    }).catch(() => {
      if (active) setEditNotice("The update was saved, but the latest AI assessment could not be refreshed. Reopen this lead to try again.");
    });
    return () => { active = false; };
  }, [activityRefreshToken, lead.id]);
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

  return <article className="lead-story" data-active-section={activeSection}>
    <header className="story-head">
      <div className="story-head-main">
        <span className="eyebrow">Complete decision trail</span>
        <h2>{lead.company_name}</h2>
        <p>{lead.full_name} · {lead.job_title || "Role not supplied"}</p>
      </div>
      {(qualification || canEdit) && <div className="story-head-side">
        {qualification && <div className={`score-orb band-${qualification.classification.toLowerCase().replaceAll("_", "-")}`}>
          <strong>{qualification.total_score}</strong>
          <span>{title(qualification.classification)}</span>
        </div>}
        {canEdit && <div className="story-head-actions">
          {!editingTitle && <button className="button-quiet" type="button" onClick={() => {
            setJobTitle(lead.job_title || ""); setEditingTitle(true); setEditNotice(null);
          }}>Edit title</button>}
          <button className="danger-button" type="button" onClick={async () => {
            if (!window.confirm("Delete this lead from the workspace? A later matching inquiry can be imported again.")) return;
            try { await deleteLead(lead.id); onDeleted?.(lead.id); }
            catch (error) { setEditNotice(error instanceof Error ? error.message : "Unable to delete lead"); }
          }}>Delete lead</button>
        </div>}
      </div>}
      {canEdit && editingTitle && <form className="story-title-form" onSubmit={async (event) => {
        event.preventDefault(); setSavingTitle(true); setEditNotice(null);
        try {
          const updated = await updateLeadTitle(lead.id, jobTitle);
          onUpdated?.(updated); setEditingTitle(false);
          setEditNotice("Title saved locally. No CRM was changed.");
        } catch (error) { setEditNotice(error instanceof Error ? error.message : "Unable to save title"); }
        finally { setSavingTitle(false); }
      }}>
        <label>Job title <input autoFocus aria-label="Job title" maxLength={255} required value={jobTitle} onChange={(event) => setJobTitle(event.target.value)} /></label>
        <button className="secondary-button" type="submit" disabled={savingTitle}>{savingTitle ? "Saving…" : "Save title"}</button>
        <button className="button-quiet" type="button" onClick={() => {
          setJobTitle(lead.job_title || ""); setEditingTitle(false); setEditNotice(null);
        }}>Cancel</button>
      </form>}
      {editNotice && <div className="notice story-head-notice" role="status">{editNotice}</div>}
    </header>
    <nav className="lead-detail-navigation" aria-label="Lead detail sections">
      <div ref={detailTabsRef} className="lead-detail-tabs" role="tablist" aria-label="Lead detail">
        {leadDetailSections.map((section, index) => <a
          key={section.id}
          id={`lead-tab-${section.id}`}
          className="lead-detail-tab"
          href={leadSectionHref(lead.id, section.id)}
          role="tab"
          aria-selected={activeSection === section.id}
          aria-controls={`lead-panel-${section.id}`}
          tabIndex={activeSection === section.id ? 0 : -1}
          onClick={(event) => {
            if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
            event.preventDefault();
            onSectionChange(section.id);
          }}
          onKeyDown={(event) => {
            let nextIndex: number | null = null;
            if (event.key === "ArrowRight") nextIndex = (index + 1) % leadDetailSections.length;
            if (event.key === "ArrowLeft") nextIndex = (index - 1 + leadDetailSections.length) % leadDetailSections.length;
            if (event.key === "Home") nextIndex = 0;
            if (event.key === "End") nextIndex = leadDetailSections.length - 1;
            if (nextIndex !== null) {
              event.preventDefault();
              const next = leadDetailSections[nextIndex];
              document.getElementById(`lead-tab-${next.id}`)?.focus();
              onSectionChange(next.id);
            }
          }}
        >{section.label}</a>)}
      </div>
      {onTogglePin && <button className={`lead-section-pin${isSectionPinned ? " is-pinned" : ""}`} type="button" aria-pressed={isSectionPinned} onClick={onTogglePin}>
        <span aria-hidden="true">{isSectionPinned ? "◆" : "◇"}</span>{isSectionPinned ? "Pinned in navigation" : "Pin this section"}
      </button>}
    </nav>
    <LeadProgressEditor
      lead={lead}
      canEdit={canEdit}
      refreshToken={activityRefreshToken}
      activeSection={activeSection}
      onConversationSynced={() => {
        setActivityRefreshToken((revision) => revision + 1);
        setMeetingRefreshToken((revision) => revision + 1);
      }}
    />
    <div id="lead-panel-follow-ups" className="lead-detail-panel" role="tabpanel" aria-labelledby="lead-tab-follow-ups" tabIndex={0} hidden={activeSection !== "follow-ups"}>
    <LeadMeetingPanel
      lead={lead}
      canEdit={canEdit}
      refreshToken={meetingRefreshToken}
      onScheduled={() => setActivityRefreshToken((revision) => revision + 1)}
    />
    {route && <section className="story-section"><div className="story-index">→</div><div><span className="eyebrow">Follow-up strategy</span><h3>How to move this lead forward</h3><div className="follow-up-plan"><div className="follow-up-goal"><span>Conversion goal</span>{route.action_plan.conversion_goal || route.action_plan.next_step_recommendation}</div><h4>Recommended sequence</h4><ol>{(route.action_plan.follow_up_strategy?.length ? route.action_plan.follow_up_strategy : [route.action_plan.immediate_action, route.action_plan.next_step_recommendation]).map((step, index) => <li key={`${index}-${step}`}>{step}</li>)}</ol>{route.manual_review && <p className="attention-callout">Validate the analysis before contacting this lead.</p>}</div></div></section>}
    <FollowUpComposer lead={lead} canEdit={canEdit} />
    </div>
    <div id="lead-panel-overview" className="lead-detail-panel" role="tabpanel" aria-labelledby="lead-tab-overview" tabIndex={0} hidden={activeSection !== "overview"}>
      <LeadLifecycle lead={lead} />
      <div className="lead-overview-grid">
        <section className="story-section">
          <div>
            <span className="eyebrow">{title(lead.source)} lead</span>
            <h3>Original inquiry</h3>
            <blockquote>{lead.message}</blockquote>
            <p className="lead-received">Received {new Date(lead.received_at).toLocaleString()}</p>
            <details className="lead-source-evidence">
              <summary>View source details</summary>
              <div className="fact-grid">
                <Fact label="Source detail" value={lead.source_detail || "Unspecified"} />
                <Fact label="External ID" value={lead.external_id || "Not supplied"} />
              </div>
            </details>
          </div>
        </section>
        <section className="story-section">
          <div>
            <span className="eyebrow">Lead profile</span>
            <h3>Contact & company</h3>
            <div className="fact-grid">
              <Fact label="Contact" value={lead.full_name} />
              <Fact label="Email" value={lead.email} />
              <Fact label="Company" value={lead.company_name} />
              <Fact label="Phone" value={lead.phone || "Not supplied"} />
              <Fact label="Industry" value={lead.industry || "Unknown"} />
              <Fact label="Geography" value={lead.geography || "Unknown"} />
              <Fact label="Team size" value={lead.employee_count ? `${lead.employee_count} people` : "Unknown"} />
              <Fact label="Requested service" value={lead.product_or_service} />
            </div>
          </div>
        </section>
      </div>
      <CollaborationPanel leadId={lead.id} canEdit={canEdit} />
      <CallsPanel leadId={lead.id} leadPhone={lead.phone} canEdit={canEdit} canCall={canCall} />
    </div>
    <div id="lead-panel-ai" className="lead-detail-panel" role="tabpanel" aria-labelledby="lead-tab-ai" tabIndex={0} hidden={activeSection !== "ai"}>
      <LeadMomentumPanel lead={lead} />
      <AiStageAssessment analysis={analysis} />
    <section className="story-section"><div className="story-index">03</div><div><span className="eyebrow">AI context / intent</span><h3>{analysis ? `${title(analysis.intent_level)} intent · ${Math.round(analysis.overall_confidence * 100)}% confidence` : "Analysis unavailable"}</h3>{analysis && <><p className="story-summary">{analysis.summary.need} {analysis.summary.pain_point}</p><div className="signal-strip"><Signal name="Urgency" value={analysis.context.urgency.value} kind={analysis.context.urgency.information_kind} /><Signal name="Timeline" value={analysis.context.explicit_timeline.value} kind={analysis.context.explicit_timeline.information_kind} /><Signal name="Budget" value={analysis.context.budget_information.value} kind={analysis.context.budget_information.information_kind} /><Signal name="Buying stage" value={analysis.context.buying_stage.value} kind={analysis.context.buying_stage.information_kind} /></div>{analysis.requires_manual_review && <div className="attention-callout"><b>Human review required</b>{analysis.error_message || "Analysis confidence needs validation."}</div>}</>}</div></section>
    <section className="story-section"><div className="story-index">04</div><div><span className="eyebrow">ICP evaluation</span><h3>{analysis?.icp_evaluation.profile_name || "Profile unavailable"}</h3><div className="icp-matrix">{icpRows.map(([name, criterion]) => <div key={name}><i className={criterion.status} /><span>{name}</span><b>{title(criterion.status)}</b><small>{criterion.rationale}</small></div>)}</div></div></section>
    <section className="story-section"><div className="story-index">05</div><div><span className="eyebrow">Qualification</span><h3>{qualification ? `${title(qualification.classification)} · Score ${qualification.total_score} of 100` : "Not qualified"}</h3>{qualification && <><div className="score-contract">{Object.entries(qualification.component_scores).map(([component, score]) => { const maximum = component === "icp_fit" ? 40 : component === "intent_urgency" ? 35 : 25; return <div key={component}><span>{title(component)}</span><b>{score}<small> of {maximum}</small></b><i><span style={{ transform: `scaleX(${score / maximum})` }} /></i></div>; })}</div><ul className="evidence-list">{qualification.rationale.map((reason) => <li key={reason}>{reason}</li>)}</ul></>}</div></section>
    <section className="story-section"><div className="story-index">06</div><div><span className="eyebrow">Routing / action plan</span><h3>{route ? `${title(route.assigned_queue)}${route.assigned_rep_name ? ` · ${route.assigned_rep_name}` : ""}` : "Routing pending"}</h3>{route && <><p className="story-summary">{route.routing_reason}</p><div className="action-command"><span>Recommended immediate action</span><b>{route.action_plan.immediate_action}</b><p>{route.action_plan.next_step_recommendation}</p></div><div className="split-list"><div><span className="eyebrow">Talking points</span>{route.action_plan.talking_points.map((item) => <p className="list-item" key={item}><i aria-hidden="true">•</i>{item}</p>)}</div><div><span className="eyebrow">Open questions</span>{route.action_plan.unanswered_questions.length ? route.action_plan.unanswered_questions.map((item) => <p className="list-item" key={item}><i aria-hidden="true">•</i>{item}</p>) : <p>No open qualification questions.</p>}</div></div></>}</div></section>
    <section className="story-section story-delivery"><div className="story-index">07</div><div><span className="eyebrow">Notification / email / CRM delivery</span><h3>External system state</h3><div className="delivery-grid"><div><span>Notification</span><b>{route ? title(route.notification_state) : "Pending"}</b><small>{route?.notifications[0]?.provider || "No provider event"}</small></div><div><span>Confirmation email</span><b>{lead.confirmation_email ? title(lead.confirmation_email.status) : "Not attempted"}</b><small>{lead.confirmation_email?.error_message || lead.confirmation_email?.recipient || "Requires a verified Gmail connection"}</small></div><div><span>CRM</span><b>{crm ? `${crmProviderLabel(crm.provider)} · ${title(crm.status)}` : "Not synchronized"}</b><small>{crm?.provider_entity_ids.contact_id || "No provider record ID"}</small></div></div>{canCrmSync && <div className="crm-action-controls">{crmProviders.length > 1 && <label>CRM provider <select aria-label="CRM provider" value={selectedProvider} onChange={(event) => setSelectedProvider(event.target.value)}><option value="">Select a CRM</option>{crmProviders.map((provider) => <option key={provider} value={provider}>{crmProviderLabel(provider)}</option>)}</select></label>}<button className="primary-button" disabled={sending || !selectedProvider} onClick={async () => { setSending(true); setSendNotice(null); try { const result = await sendLeadToCrm(lead.id, selectedProvider); setCrmResult(result); setSendNotice(result.status === "synced" ? `${crmProviderLabel(selectedProvider)} synchronized by your explicit request.` : `${crmProviderLabel(selectedProvider)} write ${title(result.status)}.`); try { onUpdated?.(await getLead(lead.id)); } catch { setSendNotice(`${crmProviderLabel(selectedProvider)} write ${title(result.status)}. Refresh lead details to see the latest state.`); } } catch (error) { setSendNotice(error instanceof Error ? error.message : "CRM write failed"); } finally { setSending(false); } }}>{sending ? "Sending…" : crm?.provider === selectedProvider && crm.status === "failed" ? `Retry ${crmProviderLabel(selectedProvider)}` : crm?.provider === selectedProvider && crm.status === "synced" ? `Update ${crmProviderLabel(selectedProvider)}` : `Send to ${crmProviderLabel(selectedProvider || "CRM")}`}</button></div>}{sendNotice && <div className="notice" role="status">{sendNotice}</div>}{crm?.last_error && <div className="attention-callout"><b>{crm.error_category || "CRM delivery issue"}</b>{crm.last_error}</div>}</div></section>
    </div>
  </article>;
}

function LeadProgressEditor({
  lead,
  canEdit,
  refreshToken,
  activeSection,
  onConversationSynced,
}: {
  lead: Lead;
  canEdit: boolean;
  refreshToken: number;
  activeSection: LeadDetailSection;
  onConversationSynced: () => void;
}) {
  const [events, setEvents] = useState<Awaited<ReturnType<typeof getLeadPipelineHistory>>>([]);
  const [eventsLoading, setEventsLoading] = useState(true);
  const [eventsError, setEventsError] = useState<string | null>(null);
  const [historyRevision, setHistoryRevision] = useState(0);
  const [stage, setStage] = useState("contacted");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [syncBusy, setSyncBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [conversionType, setConversionType] = useState<"project" | "sale">(lead.outcome?.conversion_type ?? "project");
  const [value, setValue] = useState(lead.outcome?.closed_revenue?.toString() ?? "");
  const [creditedId, setCreditedId] = useState(lead.outcome?.credited_user_id ?? lead.routing?.assigned_rep_id ?? "");
  const [members, setMembers] = useState<Awaited<ReturnType<typeof getLeadClosingOptions>>>([]);
  const [outcome, setOutcome] = useState<LeadOutcome | null>(lead.outcome ?? null);
  useEffect(() => {
    setOutcome(lead.outcome ?? null);
  }, [lead.outcome]);
  useEffect(() => {
    if (!canEdit) return;
    const controller = new AbortController();
    getLeadClosingOptions(controller.signal).then((rows) => {
      if (controller.signal.aborted) return;
      setMembers(rows);
      setCreditedId((current) => rows.some((member) => member.user_id === current) ? current : "");
    }).catch((error) => {
      if (!controller.signal.aborted) setNotice(error instanceof Error ? error.message : "Unable to load team members.");
    });
    return () => controller.abort();
  }, [lead.id, canEdit]);
  useEffect(() => {
    const controller = new AbortController();
    setEventsLoading(true);
    setEventsError(null);
    getLeadPipelineHistory(lead.id, controller.signal).then((rows) => {
      if (controller.signal.aborted) return;
      setEvents(rows);
      setEventsLoading(false);
      const latest = [...rows].reverse().find((event) => event.stage === "manual_progress");
      if (latest) {
        const savedStage = latest.details.stage;
        if (typeof savedStage === "string") setStage(savedStage);
      }
    }).catch((error) => {
      if (controller.signal.aborted) return;
      setEventsError(error instanceof Error ? error.message : "Unable to load the lead history.");
      setEventsLoading(false);
    });
    return () => controller.abort();
  }, [lead.id, refreshToken, historyRevision]);
  const progressEvents = events.filter((event) => event.stage === "manual_progress").reverse();
  const emailEvents = events.filter((event) => event.stage === "gmail_message").reverse();
  const meetingEvents = events.filter((event) => event.stage === "meeting_scheduled").reverse();
  const syncConversation = async () => {
    setSyncBusy(true); setNotice(null);
    try {
      const result = await syncLeadGmailConversation(lead.id);
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
      const closing = stage === "converted" || stage === "lost";
      const result = await updateLeadProgress(lead.id, {
        stage, note,
        ...(stage === "converted" ? { conversion_type: conversionType, closed_revenue: value === "" ? null : Number(value) } : {}),
        ...(closing ? { credited_user_id: creditedId || null } : {}),
      });
      setNote("");
      if (closing) {
        setOutcome(result.outcome ?? null);
        setNotice(stage === "converted"
          ? `${title(conversionType)} win recorded in Corral. Qualification is unchanged; no CRM record was created.`
          : "Closed / lost recorded. Qualification is unchanged.");
      } else {
        setNotice(`Progress saved. AI re-evaluated this lead as ${title(result.classification)} (${result.score}/100).`);
      }
      onConversationSynced();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Unable to update lead progress");
    } finally { setBusy(false); }
  };
  return <>
  <section id="lead-panel-closing" className="story-section lead-progress" role="tabpanel" aria-labelledby="lead-tab-closing" tabIndex={0} hidden={activeSection !== "closing"}><div className="story-index">↗</div><div>
    <span className="eyebrow">Comments & closing</span><h3>Record sales progress or close this lead</h3>
    <p className="story-summary">Add a verified comment with each update. Choose Converted to record a Project or Sale, or Closed / lost to capture the outcome. Closing does not rerun AI or create an external CRM record.</p>
    {!canEdit && <AiStageAssessment analysis={lead.analysis} />}
    {outcome && <LeadOutcomeReceipt outcome={outcome} canEdit={canEdit} busy={busy} onRetry={async () => {
      setBusy(true); setNotice(null);
      try { setOutcome(await retryLeadCongratulations(lead.id)); }
      catch (error) { setNotice(error instanceof Error ? error.message : "Unable to retry congratulations."); }
      finally { setBusy(false); }
    }} />}
    {canEdit && <div className="lead-progress-sync"><button className="secondary-button" type="button" disabled={syncBusy || busy} onClick={() => void syncConversation()}>{syncBusy ? "Syncing Gmail thread…" : "Sync Gmail conversation & re-check"}</button><small>Read-only sync for messages exchanged with this lead. Imported message text is used as qualification context.</small></div>}
    {canEdit && <form className="lead-progress-form" onSubmit={(event) => void save(event)}>
      <div className="lead-stage-context">
      <AiStageAssessment analysis={lead.analysis} />
      <label className="follow-up-field">Manual progress or closing outcome<select value={stage} onChange={(event) => setStage(event.target.value)}>
        <option value="contacted">Contacted</option><option value="responded">Responded</option><option value="meeting_scheduled">Meeting scheduled</option><option value="proposal_sent">Proposal sent</option><option value="converted">Converted → Project or Sale</option><option value="lost">Closed / lost</option>
      </select></label>
      </div>
      {stage === "converted" && <div className="lead-closing-fields">
        <label className="follow-up-field">Successful outcome<select value={conversionType} onChange={(event) => setConversionType(event.target.value as "project" | "sale")}><option value="project">Project</option><option value="sale">Sale</option></select></label>
        <label className="follow-up-field">Recorded value (USD, optional)<input type="number" min="0" step="0.01" max="999999999999.99" value={value} onChange={(event) => setValue(event.target.value)} placeholder="Leave blank if unknown" /></label>
      </div>}
      {(stage === "converted" || stage === "lost") && <label className="follow-up-field">Credited team member<select value={creditedId} onChange={(event) => setCreditedId(event.target.value)}><option value="">Use assigned member if linked</option>{members.map((member) => <option key={member.user_id} value={member.user_id}>{member.display_name}{!member.slack_linked ? " · Slack link needed" : ""}</option>)}</select><small>{creditedId ? "This member receives analytics credit. Slack DMs require their member link in Users & permissions." : "Uses the assigned workspace member when linked. Otherwise the win is uncredited; choose a member for individual analytics and a direct congratulations."}</small></label>}
      <label className="follow-up-field">Comments · verified update<textarea value={note} onChange={(event) => setNote(event.target.value)} rows={3} minLength={3} maxLength={2000} placeholder="Example: Replied asking for a proposal by Friday; timeline is now this quarter." required /></label>
      {outcome && ["closed_won", "closed_lost"].includes(outcome.pipeline_stage) && <small className="closing-edit-note">A closing stage corrects the existing outcome. Already-sent congratulations will not be sent again.</small>}
      <button className="secondary-button" type="submit" disabled={busy || note.trim().length < 3}>{busy ? "Saving…" : stage === "converted" ? `Record ${title(conversionType)} win` : stage === "lost" ? "Record closed / lost" : "Save progress & re-check lead"}</button>
    </form>}
    {notice && <div className="notice" role="status">{notice}</div>}
  </div></section>
  <section id="lead-panel-history" className="lead-detail-panel lead-history-panel" role="tabpanel" aria-labelledby="lead-tab-history" tabIndex={0} hidden={activeSection !== "history"}>
    {eventsLoading && <div className="lead-history-loading" aria-label="Loading lead history"><i /><i /><i /></div>}
    {eventsError && <div className="lead-history-error" role="alert"><b>History could not be loaded</b><span>{eventsError}</span><button className="secondary-button" type="button" onClick={() => setHistoryRevision((revision) => revision + 1)}>Try again</button></div>}
    {progressEvents.length > 0 && <div className="lead-progress-history"><h4>Progress history</h4>{progressEvents.slice(0, 5).map((event) => <article key={event.id}><b>{title(String(event.details.stage || event.status))}</b><time>{new Date(event.created_at).toLocaleString()}</time><p>{String(event.details.note || "")}</p><small>AI assessment: {title(String((event.details.qualification as { classification?: string } | undefined)?.classification || "pending"))} · {String((event.details.qualification as { score?: number } | undefined)?.score ?? "—")}/100</small></article>)}</div>}
    {emailEvents.length > 0 && <div className="lead-progress-history"><h4>Gmail conversation history</h4>{emailEvents.slice(0, 10).map((event) => <article key={event.id}><b>{event.details.direction === "outbound" ? "You sent" : "Lead sent"}: {String(event.details.subject || "(no subject)")}</b><time>{new Date(event.created_at).toLocaleString()}</time><p>{String(event.details.body || "")}</p><small>{String(event.details.sender || "")} · {String(event.details.recipients || "")}</small></article>)}</div>}
    {meetingEvents.length > 0 && <div className="lead-progress-history"><h4>Scheduled meetings</h4>{meetingEvents.slice(0, 10).map((event) => {
      const start = String(event.details.start_at || "");
      const provider = String(event.details.provider || "");
      const eventUrl = event.details.event_url;
      return <article key={event.id}><b>{String(event.details.title || "Meeting")} · {title(provider)}</b><time>{start ? new Date(start).toLocaleString() : new Date(event.created_at).toLocaleString()}</time><p>Scheduled from the Gmail conversation. The connected workspace account is the host.</p>{typeof eventUrl === "string" && eventUrl && <a href={eventUrl} target="_blank" rel="noreferrer">Open event</a>}</article>;
    })}</div>}
    {!eventsLoading && !eventsError && !progressEvents.length && !emailEvents.length && !meetingEvents.length && <p className="lead-history-empty">No recorded sales progress, Gmail messages, or scheduled meetings yet.</p>}
  </section>
  </>;
}

function LeadOutcomeReceipt({ outcome, canEdit, busy, onRetry }: {
  outcome: LeadOutcome; canEdit: boolean; busy: boolean; onRetry: () => Promise<void>;
}) {
  const won = outcome.pipeline_stage === "closed_won";
  if (!won && outcome.pipeline_stage !== "closed_lost") return null;
  const retryable = outcome.notifications.some((delivery) => ["failed", "retryable", "not_configured"].includes(delivery.status));
  return <div className={`lead-outcome-receipt ${won ? "won" : "lost"}`} role="status">
    <h4>{won ? `Congratulations, ${outcome.credited_name ?? "team"}!` : "Lead closed / lost"}</h4>
    <p>{won ? `${title(outcome.conversion_type ?? "closed_won")} recorded` : "Lost outcome recorded"}
      {outcome.closed_revenue !== null && won ? ` · ${outcome.currency} ${outcome.closed_revenue.toLocaleString(undefined, { minimumFractionDigits: 2 })}` : won ? " · Value not recorded" : ""}
      {outcome.closed_at ? ` · ${new Date(outcome.closed_at).toLocaleDateString()}` : ""}</p>
    {won && outcome.notifications.map((delivery) => <div className="win-delivery" key={delivery.destination}>
      <b>{delivery.destination === "channel" ? "Team channel" : "Member direct message"}</b>
      <span>{title(delivery.status)}</span>{delivery.error_message && <small>{delivery.error_message}</small>}
    </div>)}
    {won && retryable && canEdit && <button className="secondary-button" disabled={busy} onClick={() => void onRetry()}>{busy ? "Retrying…" : "Retry unsent congratulations"}</button>}
  </div>;
}

function FollowUpComposer({ lead, canEdit }: { lead: Lead; canEdit: boolean }) {
  const [draft, setDraft] = useState<FollowUpEmail | null>(null);
  const [history, setHistory] = useState<FollowUpEmail[]>([]);
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
    getLeadFollowUpHistory(lead.id, controller.signal).then((value) => {
      if (!controller.signal.aborted) setHistory(value);
    }).catch(() => { if (!controller.signal.aborted) setNotice("Follow-up history could not be loaded."); });
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
      try { setHistory(await getLeadFollowUpHistory(lead.id)); }
      catch { setNotice(`${success} History could not be refreshed.`); }
    } catch (error) { setNotice(error instanceof Error ? error.message : "Follow-up action failed"); }
    finally { setBusy(false); }
  };
  const statusLabel = (value: string) => value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
  return <section className="story-section follow-up-composer"><div className="story-index">FU</div><div>
    <span className="eyebrow">AI follow-up · human approved</span><h3>Draft a personal next step</h3>
    {!draft && <><p className="story-summary">Create and edit a lead-specific follow-up here. Nothing is sent until you review it and click Send follow-up; Corral uses the connected Gmail account.</p>{blockedReason && <div className="attention-callout follow-up-blocked"><b>{disqualified ? "Outreach paused · disqualified" : "Outreach paused · review required"}</b>{blockedReason}</div>}{canEdit && <button className="secondary-button" disabled={busy || Boolean(blockedReason)} onClick={() => void run(() => generateLeadFollowUp(lead.id), "Draft created. Review and edit it, then click Send follow-up to send through connected Gmail.")}>{busy ? "Preparing draft…" : "Generate follow-up draft"}</button>}</>}
    {draft && <>
      {draft.status === "sent" && canEdit && <button className="secondary-button" disabled={busy || Boolean(blockedReason)} onClick={() => void run(() => generateLeadFollowUp(lead.id), "Another AI draft is ready. Review and edit it before sending.")}>{busy ? "Preparing draft…" : "Draft another follow-up"}</button>}
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
    {history.filter((email) => email.id !== draft?.id && email.status === "sent").length > 0 && <details className="follow-up-history"><summary>Previous sent follow-ups</summary>{history.filter((email) => email.id !== draft?.id && email.status === "sent").map((email) => <article key={email.id}><strong>{email.subject}</strong><p>Sent {email.sent_at ? new Date(email.sent_at).toLocaleString() : "—"} · {email.recipient}</p><p style={{ whiteSpace: "pre-wrap" }}>{email.body}</p></article>)}</details>}
    {notice && <div className="notice" role="status">{notice}</div>}
  </div></section>;
}

function Fact({ label, value }: { label: string; value: string }) { return <div className="fact"><span>{label}</span><b>{value}</b></div>; }
function Signal({ name, value, kind }: { name: string; value: string | null; kind: string }) { return <div className={`signal ${kind}`}><span>{name} · {kind}</span><b>{value || "Unknown"}</b></div>; }
