import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  deleteLead,
  getLead,
  getLeadClosingOptions,
  getLeadMomentum,
  getLeadPipelineHistory,
  listIntegrations,
  retryLeadCongratulations,
  retryLeadNotification,
  sendLeadToCrm,
  syncLeadGmailConversation,
  updateLeadAssignment,
  updateLeadProfile,
  updateLeadProgress,
} from "../../api";
import { AiStageAssessment } from "../../components/AiStageAssessment";
import { CallsPanel } from "../../components/CallsPanel";
import { CollaborationPanel } from "../../components/CollaborationPanel";
import { EmailComposer } from "../../components/EmailComposer";
import { GmailInquiryView } from "../../components/GmailInquiryView";
import { LeadAssistantPanel } from "../../components/LeadAssistantPanel";
import { leadAction } from "../shell/events";
import { LeadEmailPanel } from "../../components/LeadEmailPanel";
import { LeadMeetingPanel } from "../../components/LeadMeetingPanel";
import { componentTotals } from "../../components/record/scoreModel";
import { TOGGLE_AGENT_EVENT } from "../../components/shell/agentEvents";
import { getLeadTalkingPoints } from "../../leadActionPlan";
import { leadSectionHref, type LeadDetailSection } from "../../leadSections";
import { buildLeadProfileUpdates, createLeadProfileDraft, type LeadProfileDraft } from "../../leadProfileEditor";
import type { CrmSync, Lead, LeadAssignmentOption, LeadMomentum, LeadOutcome, ProviderConnection, Qualification } from "../../types";
import { dealActions, flushDealChanges, getDeal, formatDuration, formatMoney, stageInfo, useDeals, viewDeal, type DealStage } from "../deals/dealStore";
import { StageMoveSheet } from "../deals/StageMoveSheet";
import { togglePhone, type LeadAction } from "../shell/events";
import { Icon } from "../ui/Icon";
import {
  BandBadge,
  Banner,
  Button,
  EmptyState,
  formatShortDate,
  Group,
  Menu,
  MenuItem,
  MenuSeparator,
  Monogram,
  Row,
  ScoreRing,
  titleize,
} from "../ui/primitives";
import { DealRoom, StageRail } from "./DealRoom";
import { providerLabel, queueLabel, sourceLabel } from "./leadFormat";

/** The record's sections, in the order a person works a lead. */
const TABS: Array<{ id: LeadDetailSection; label: string }> = [
  { id: "overview", label: "Overview" },
  { id: "deal", label: "Deal" },
  { id: "calls", label: "Calls" },
  { id: "email", label: "Email" },
  { id: "follow-ups", label: "Follow-up" },
  { id: "ai", label: "Qualification" },
  { id: "history", label: "Activity" },
  { id: "agent", label: "Ask Corral" },
];

export type LeadIntent = { action: LeadAction; nonce: number } | null;

type Props = {
  lead: Lead;
  canCrmSync?: boolean;
  canEdit?: boolean;
  canViewEmail?: boolean;
  canUseAgent?: boolean;
  canCall?: boolean;
  canManageRouting?: boolean;
  assignmentOptions?: LeadAssignmentOption[];
  recordedStage?: string | null;
  intent?: LeadIntent;
  embeddedPhone?: boolean;
  dialerPhoneConnectionId?: string | null;
  onNavigateToDialer: () => void;
  canEnrollAutomations?: boolean;
  onEnrollAutomation?: (kind: "sequence" | "playbook") => void;
  onUpdated?: (lead: Lead) => void;
  onDeleted?: (leadId: string) => void;
  activeSection: LeadDetailSection;
  onSectionChange: (section: LeadDetailSection) => void;
  isSectionPinned?: boolean;
  onTogglePin?: () => void;
  onBack?: () => void;
};

export function LeadRecord({
  lead,
  canCrmSync = false,
  canEdit = false,
  canViewEmail = false,
  canUseAgent = false,
  canCall = false,
  canManageRouting = false,
  assignmentOptions = [],
  recordedStage = null,
  intent = null,
  embeddedPhone = false,
  dialerPhoneConnectionId = null,
  onNavigateToDialer,
  canEnrollAutomations = false,
  onEnrollAutomation,
  onUpdated,
  onDeleted,
  activeSection,
  onSectionChange,
  isSectionPinned = false,
  onTogglePin,
  onBack,
}: Props) {
  const [editingProfile, setEditingProfile] = useState(false);
  const [profileDraft, setProfileDraft] = useState<LeadProfileDraft>(() => createLeadProfileDraft(lead));
  const [savingProfile, setSavingProfile] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [meetingRefresh, setMeetingRefresh] = useState(0);
  const [activityRefresh, setActivityRefresh] = useState(0);
  const [compose, setCompose] = useState<{ subject?: string; body?: string; quoteId?: string; nonce: number } | null>(null);
  const [newQuote, setNewQuote] = useState(false);
  const [move, setMove] = useState<DealStage | null>(null);
  const [assigning, setAssigning] = useState(false);
  const store = useDeals();
  const dealView = viewDeal(lead, store.deals[lead.id], recordedStage);
  const tabsRef = useRef<HTMLDivElement>(null);
  const tabs = TABS.filter((tab) => (tab.id !== "email" || canViewEmail));
  const shownSection: LeadDetailSection = activeSection === "closing" ? "history" : activeSection;
  const updatedRef = useRef(onUpdated);
  useEffect(() => { updatedRef.current = onUpdated; }, [onUpdated]);
  useEffect(() => {
    if (activeSection === "email" && !canViewEmail) onSectionChange("overview");
  }, [activeSection, canViewEmail, onSectionChange]);
  useEffect(() => {
    if (!activityRefresh || !updatedRef.current) return;
    let active = true;
    getLead(lead.id)
      .then((updated) => { if (active) updatedRef.current?.(updated); })
      .catch(() => { if (active) setNotice("The update was saved, but the latest assessment couldn’t be refreshed. Reopen this lead to try again."); });
    return () => { active = false; };
  }, [activityRefresh, lead.id]);
  useEffect(() => {
    const strip = tabsRef.current;
    const current = strip?.querySelector<HTMLElement>('[aria-selected="true"]');
    if (strip && current && strip.scrollWidth > strip.clientWidth) {
      strip.scrollTo({ left: current.offsetLeft - (strip.clientWidth - current.offsetWidth) / 2, behavior: "smooth" });
    }
  }, [activeSection]);
  // Actions requested from elsewhere (a pipeline card, the Today list, the command bar).
  useEffect(() => {
    if (!intent) return;
    if (intent.action === "email" && canEdit) setCompose({ nonce: intent.nonce });
    if (intent.action === "quote" && canEdit) setNewQuote(true);
    if (intent.action === "call" && embeddedPhone) togglePhone(true);
  }, [intent, canEdit, embeddedPhone]);

  const analysis = lead.analysis;
  const qualification = lead.qualification;
  const route = lead.routing;
  const talkingPoints = getLeadTalkingPoints(route);
  const review = Boolean(route?.manual_review || analysis?.requires_manual_review);
  const reviewReasons = manualReviewReasons(lead);
  const synced = () => {
    setActivityRefresh((value) => value + 1);
    setMeetingRefresh((value) => value + 1);
  };

  const deleteThisLead = async () => {
    if (!window.confirm("Delete this lead from the workspace? A later matching inquiry can be imported again.")) return;
    try {
      await deleteLead(lead.id);
      onDeleted?.(lead.id);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Unable to delete lead");
    }
  };
  const assign = async (repId: string | null) => {
    setAssigning(true);
    setNotice(null);
    try {
      onUpdated?.(await updateLeadAssignment(lead.id, repId));
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Unable to update the owner.");
    } finally {
      setAssigning(false);
    }
  };
  const startCall = () => {
    if (embeddedPhone) {
      togglePhone(true);
      return;
    }
    onNavigateToDialer();
  };
  const startEmail = (seed?: { subject?: string; body?: string; quoteId?: string }) => {
    onSectionChange("email");
    setCompose({ ...seed, nonce: Date.now() });
  };
  const setProfileField = (field: keyof LeadProfileDraft, value: string) => {
    setProfileDraft((current) => ({ ...current, [field]: value }));
  };

  const title = lead.company_name || lead.full_name || "Unnamed lead";
  const owner = route ? route.assigned_rep_name || "Unassigned" : "Routing pending";
  const slaDue = dealView.slaDue;
  return (
    <article className="v-record" data-active-section={shownSection}>
      <header className="v-record-head">
        {onBack && (
          <button type="button" className="v-crumb-back v-record-back" onClick={onBack} aria-label="Back to leads">
            <Icon name="chevronLeft" size={18} />
            Leads
          </button>
        )}
        <div className="v-record-identity">
          <Monogram text={title} size={52} />
          <div className="v-record-names">
            <h1 className="v-record-title">{title}</h1>
            <p className="v-record-sub">
              {lead.full_name || "Contact not supplied"}
              {lead.job_title ? <span className="v-sep-dot">{lead.job_title}</span> : null}
              <span className="v-sep-dot">{sourceLabel(lead.source)}</span>
              <span className="v-sep-dot">
                <time dateTime={lead.received_at} title={new Date(lead.received_at).toLocaleString()}>{formatShortDate(lead.received_at)}</time>
              </span>
            </p>
          </div>
        </div>
        <div className="v-record-actions">
          {canCall && (
            <Button kind="filled" icon="phone" onClick={startCall} disabled={!lead.phone} title={lead.phone ? `Call ${lead.phone}` : "No phone number on record"}>Call</Button>
          )}
          {canViewEmail && canEdit && (
            <Button kind="tinted" icon="mail" onClick={() => startEmail()} disabled={!lead.email} title={lead.email ? `Email ${lead.email}` : "No email address on record"}>Email</Button>
          )}
          {canEdit && <Button icon="quote" onClick={() => { onSectionChange("deal"); setNewQuote(true); }}>Quote</Button>}
          {canUseAgent && (
            <Button kind="gray" icon="sparkle" onClick={() => window.dispatchEvent(new CustomEvent(TOGGLE_AGENT_EVENT, { detail: { open: true } }))}>
              Ask Corral
            </Button>
          )}
          <Menu label="More lead actions" iconOnly="more" trigger={null}>
            {(close) => (
              <>
                {canEnrollAutomations && (
                  <>
                    <MenuItem icon="automations" aria-label={`Enroll ${lead.full_name || "lead"} in a sequence`} onSelect={() => { close(); onEnrollAutomation?.("sequence"); }}>Enroll in a sequence…</MenuItem>
                    <MenuItem icon="layers" aria-label={`Enroll ${lead.full_name || "lead"} in a playbook`} onSelect={() => { close(); onEnrollAutomation?.("playbook"); }}>Enroll in a playbook…</MenuItem>
                    <MenuSeparator />
                  </>
                )}
                {onTogglePin && (
                  <MenuItem icon="pin" aria-pressed={isSectionPinned} onSelect={() => { close(); onTogglePin(); }}>
                    {isSectionPinned ? "Unpin this section" : "Pin this section to the sidebar"}
                  </MenuItem>
                )}
                {canEdit && (
                  <>
                    <MenuItem icon="edit" onSelect={() => { close(); setProfileDraft(createLeadProfileDraft(lead)); setEditingProfile(true); setNotice(null); }}>Edit lead details</MenuItem>
                    <MenuSeparator />
                    <MenuItem icon="trash" danger onSelect={() => { close(); void deleteThisLead(); }}>Delete lead…</MenuItem>
                  </>
                )}
              </>
            )}
          </Menu>
        </div>
      </header>

      <section className="v-record-facts" aria-label="At a glance">
        {canManageRouting && route ? (
          <Menu
            label="Change owner"
            triggerKind="plain"
            triggerClassName="v-fact v-fact-btn"
            trigger={
              <>
                <span className="v-fact-label">Owner</span>
                <span className="v-fact-value"><span className="b-avatar" aria-hidden="true">{owner.slice(0, 1)}</span>{assigning ? "Saving…" : owner}<Icon name="chevronDown" size={12} /></span>
              </>
            }
          >
            {(close) => (
              <>
                <span className="v-menu-label">Assign to</span>
                {assignmentOptions.map((option) => (
                  <MenuItem key={option.user_id} icon={option.user_id === route.assigned_rep_id ? "check" : undefined} onSelect={() => { close(); void assign(option.user_id); }}>{option.display_name}</MenuItem>
                ))}
                <MenuSeparator />
                <MenuItem onSelect={() => { close(); void assign(null); }}>Unassigned</MenuItem>
              </>
            )}
          </Menu>
        ) : (
          <div className="v-fact">
            <span className="v-fact-label">Owner</span>
            <span className="v-fact-value"><span className="b-avatar" aria-hidden="true">{owner.slice(0, 1)}</span>{owner}</span>
          </div>
        )}
        <button type="button" className="v-fact v-fact-btn" onClick={() => onSectionChange("deal")}>
          <span className="v-fact-label">Deal value</span>
          <span className="v-fact-value num">{dealView.value != null ? formatMoney(dealView.value) : "Not set"}</span>
        </button>
        <div className="v-fact">
          <span className="v-fact-label">Stage</span>
          <span className="v-fact-value"><i className="v-stage-dot" data-stage={dealView.stage} aria-hidden="true" />{stageInfo(dealView.stage).label}{dealView.daysInStage != null && dealView.stage !== "won" && dealView.stage !== "lost" ? <small className="num">· {dealView.daysInStage}d</small> : null}</span>
        </div>
        {slaDue ? (
          <div className="v-fact" data-tone={slaDue.overdue ? "danger" : "attention"}>
            <span className="v-fact-label">First response</span>
            <span className="v-fact-value"><Icon name="timer" size={14} />{slaDue.overdue ? `${formatDuration(slaDue.minutes)} overdue` : `due in ${formatDuration(slaDue.minutes)}`}</span>
          </div>
        ) : (
          <div className="v-fact">
            <span className="v-fact-label">Queue</span>
            <span className="v-fact-value">{route ? queueLabel(route.assigned_queue) : "—"}</span>
          </div>
        )}
        {qualification && (
          <button
            type="button"
            className="v-fact v-fact-btn v-fact-score"
            onClick={() => onSectionChange("ai")}
            aria-label={`Qualification score ${qualification.total_score}, ${review ? "manual review hold" : titleize(qualification.classification)}. Open qualification details.`}
          >
            <ScoreRing score={qualification.total_score} band={qualification.classification} review={review} size={34} label="" />
            <span className="v-fact-score-text">
              <BandBadge band={qualification.classification} review={review} />
              <small>{qualification.icp_source === "not_configured" ? "Intent + engagement" : "ICP + intent + engagement"}</small>
            </span>
          </button>
        )}
      </section>

      <StageRail view={dealView} canEdit={canEdit} onMove={(target) => setMove(target)} />

      {canEdit && editingProfile && (
        <form
          className="v-card v-card-pad v-profile-editor"
          onSubmit={async (event) => {
            event.preventDefault();
            const { updates, error: validationError } = buildLeadProfileUpdates(lead, profileDraft);
            if (validationError) {
              setNotice(validationError);
              return;
            }
            if (Object.keys(updates).length === 0) {
              setEditingProfile(false);
              setNotice("No lead details changed.");
              return;
            }
            setSavingProfile(true);
            setNotice(null);
            try {
              const updated = await updateLeadProfile(lead.id, updates);
              onUpdated?.(updated);
              setEditingProfile(false);
              setNotice("Lead details saved and qualification refreshed. No CRM was changed.");
            } catch (error) {
              setNotice(error instanceof Error ? error.message : "Unable to save lead details.");
            } finally {
              setSavingProfile(false);
            }
          }}
        >
          <div className="v-card-headrow">
            <div>
              <h2 className="v-card-title">Edit lead details</h2>
              <p className="v-card-sub">Saving refreshes qualification but keeps the original inquiry intact. CRM data changes only when you request a sync.</p>
            </div>
            <div className="v-profile-editor-actions">
              <Button disabled={savingProfile} onClick={() => setEditingProfile(false)}>Cancel</Button>
              <Button kind="filled" type="submit" disabled={savingProfile}>{savingProfile ? "Saving…" : "Save changes"}</Button>
            </div>
          </div>
          <div className="v-profile-editor-grid">
            <label className="v-field">
              <span>Company name</span>
              <input autoFocus maxLength={255} value={profileDraft.company_name} onChange={(event) => setProfileField("company_name", event.target.value)} />
            </label>
            <label className="v-field">
              <span>Contact name</span>
              <input maxLength={255} value={profileDraft.full_name} onChange={(event) => setProfileField("full_name", event.target.value)} />
            </label>
            <label className="v-field">
              <span>Job title</span>
              <input maxLength={255} value={profileDraft.job_title} onChange={(event) => setProfileField("job_title", event.target.value)} />
            </label>
            <label className="v-field">
              <span>Email</span>
              <input type="email" maxLength={320} value={profileDraft.email} onChange={(event) => setProfileField("email", event.target.value)} />
            </label>
            <label className="v-field">
              <span>Phone</span>
              <input type="tel" maxLength={64} value={profileDraft.phone} onChange={(event) => setProfileField("phone", event.target.value)} />
            </label>
            <label className="v-field">
              <span>Industry</span>
              <input maxLength={255} value={profileDraft.industry} onChange={(event) => setProfileField("industry", event.target.value)} />
            </label>
            <label className="v-field">
              <span>Company size</span>
              <input type="number" min={0} step={1} value={profileDraft.employee_count} onChange={(event) => setProfileField("employee_count", event.target.value)} />
            </label>
            <label className="v-field">
              <span>Annual revenue</span>
              <input type="number" min={0} step="0.01" value={profileDraft.annual_revenue} onChange={(event) => setProfileField("annual_revenue", event.target.value)} />
            </label>
            <label className="v-field">
              <span>Geography</span>
              <input maxLength={255} value={profileDraft.geography} onChange={(event) => setProfileField("geography", event.target.value)} />
            </label>
            <label className="v-field">
              <span>Budget</span>
              <input type="number" min={0} step="0.01" value={profileDraft.budget} onChange={(event) => setProfileField("budget", event.target.value)} />
            </label>
            <label className="v-field v-profile-editor-wide">
              <span>Product or service</span>
              <input maxLength={255} value={profileDraft.product_or_service} onChange={(event) => setProfileField("product_or_service", event.target.value)} />
            </label>
          </div>
          <div className="v-profile-editor-actions v-profile-editor-bottom">
            <Button disabled={savingProfile} onClick={() => setEditingProfile(false)}>Cancel</Button>
            <Button kind="filled" type="submit" disabled={savingProfile}>{savingProfile ? "Saving…" : "Save changes"}</Button>
          </div>
        </form>
      )}
      {notice && <Banner title={notice} action={<Button kind="plain" size="sm" onClick={() => setNotice(null)}>Dismiss</Button>} />}

      {review && (
        <div className="v-hold" role="status" aria-labelledby={`hold-${lead.id}`}>
          <span className="v-hold-icon"><Icon name="hand" size={18} /></span>
          <div>
            <b id={`hold-${lead.id}`}>Waiting for a person to review</b>
            <p>{reviewReasons[0] ?? "This lead matched one of your review rules. Check the details and analysis before contacting them."}</p>
            {reviewReasons.length > 1 && (
              <details>
                <summary>{reviewReasons.length - 1} more matched condition{reviewReasons.length > 2 ? "s" : ""}</summary>
                <ul>{reviewReasons.slice(1).map((reason) => <li key={reason}>{reason}</li>)}</ul>
              </details>
            )}
          </div>
        </div>
      )}
      {analysis?.status === "provider_error" && (
        <Banner tone="attention" title="The AI analysis didn’t complete">
          A rules-based fallback assessment was used. It appears in Review only when that rule is enabled or another rule applies.
        </Banner>
      )}

      <div className="v-tabs" ref={tabsRef} role="tablist" aria-label="Lead sections">
        {tabs.map((tab, index) => (
          <a
            key={tab.id}
            id={`lead-tab-${tab.id}`}
            className="v-tab-pill"
            href={leadSectionHref(lead.id, tab.id)}
            role="tab"
            aria-selected={shownSection === tab.id}
            aria-controls={`lead-panel-${tab.id}`}
            tabIndex={shownSection === tab.id ? 0 : -1}
            onClick={(event) => {
              if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
              event.preventDefault();
              onSectionChange(tab.id);
            }}
            onKeyDown={(event) => {
              let next: number | null = null;
              if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
              if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
              if (event.key === "Home") next = 0;
              if (event.key === "End") next = tabs.length - 1;
              if (next === null) return;
              event.preventDefault();
              document.getElementById(`lead-tab-${tabs[next].id}`)?.focus();
              onSectionChange(tabs[next].id);
            }}
          >
            {tab.id === "agent" && <Icon name="sparkle" size={14} />}
            {tab.label}
            {tab.id === "deal" && dealView.deal.quotes.length > 0 && <span className="v-tab-count num">{dealView.deal.quotes.length}</span>}
          </a>
        ))}
      </div>

      {/* -------------------------------------------------------------- Deal */}
      <div id="lead-panel-deal" className="v-panel" role="tabpanel" aria-labelledby="lead-tab-deal" tabIndex={0} hidden={shownSection !== "deal"}>
        {shownSection === "deal" && (
          <DealRoom
            lead={lead}
            view={dealView}
            canEdit={canEdit}
            canEmail={canViewEmail && canEdit && Boolean(lead.email)}
            onEmailQuote={(subject, body, quoteId) => startEmail({ subject, body, quoteId })}
            onMoved={(fresh, message) => { setNotice(message); if (fresh) onUpdated?.(fresh); else synced(); }}
            startNewQuote={newQuote}
            onQuoteStarted={() => setNewQuote(false)}
          />
        )}
      </div>

      {/* ------------------------------------------------------------- Calls */}
      <div id="lead-panel-calls" className="v-panel" role="tabpanel" aria-labelledby="lead-tab-calls" tabIndex={0} hidden={shownSection !== "calls"}>
        {shownSection === "calls" && (
          <div className="v-legacy-block">
            <CallsPanel
              leadId={lead.id}
              leadName={lead.full_name}
              companyName={lead.company_name}
              representativeName={route?.assigned_rep_name ?? null}
              leadPhone={lead.phone}
              canCall={canCall}
              canEdit={canEdit}
              historyOnly
            />
          </div>
        )}
      </div>

      {/* ---------------------------------------------------------- Overview */}
      <div id="lead-panel-overview" className="v-panel" role="tabpanel" aria-labelledby="lead-tab-overview" tabIndex={0} hidden={shownSection !== "overview"}>
        <div className="v-overview">
          <div className="v-overview-main">
            <section className="v-card v-card-pad">
              <h2 className="v-card-title">{lead.source_detail === "manual-control-center" ? "Inquiry and context" : "Original inquiry"}</h2>
              <p className="v-card-sub">From {sourceLabel(lead.source)} · {new Date(lead.received_at).toLocaleString()}</p>
              <div className="v-inquiry">
                <GmailInquiryView
                  message={lead.message}
                  source={lead.source}
                  leadEmail={lead.email}
                  receivedAt={lead.received_at}
                  displayAsContext={lead.source_detail === "manual-control-center"}
                />
              </div>
            </section>
            {analysis && (
              <section className="v-card v-card-pad v-gist">
                <h2 className="v-card-title">In short</h2>
                <dl>
                  {analysis.summary.need && <div><dt>Need</dt><dd>{analysis.summary.need}</dd></div>}
                  {analysis.summary.pain_point && <div><dt>Pain</dt><dd>{analysis.summary.pain_point}</dd></div>}
                  {analysis.summary.urgency && <div><dt>Urgency</dt><dd>{analysis.summary.urgency}</dd></div>}
                  {analysis.summary.potential_concern && <div><dt>Watch for</dt><dd>{analysis.summary.potential_concern}</dd></div>}
                </dl>
              </section>
            )}
            <div className="v-collab">
              <CollaborationPanel leadId={lead.id} canEdit={canEdit} />
            </div>
          </div>
          <aside className="v-overview-side">
            <Group title="Contact">
              <Row icon="person" label="Name">{lead.full_name || "Not supplied"}</Row>
              <Row icon="mail" label="Email">{lead.email ? <a href={`mailto:${lead.email}`}>{lead.email}</a> : "Not supplied"}</Row>
              <Row icon="phone" label="Phone">{lead.phone || "Not supplied"}</Row>
            </Group>
            <Group title="Company">
              <Row icon="building" label="Company">{lead.company_name || "Not supplied"}</Row>
              <Row icon="layers" label="Industry">{lead.industry || "Unknown"}</Row>
              <Row icon="globe" label="Geography">{lead.geography || "Unknown"}</Row>
              <Row icon="team" label="Team size">{lead.employee_count ? `${lead.employee_count.toLocaleString()} people` : "Unknown"}</Row>
              <Row icon="target" label="Wants">{lead.product_or_service || "Not stated"}</Row>
            </Group>
            <Group title="Ownership">
              <Row icon="person" label="Owner">{route ? route.assigned_rep_name || "Unassigned" : "Routing pending"}</Row>
              <Row icon="route" label="Queue">{route ? queueLabel(route.assigned_queue) : "—"}</Row>
              <Row icon="flag" label="Outcome">{lead.outcome ? titleize(lead.outcome.pipeline_stage) : "Open"}</Row>
            </Group>
            <ProgressList lead={lead} />
            <details className="v-disclosure">
              <summary>Source details</summary>
              <Group>
                <Row label="Source detail">{lead.source_detail || "Unspecified"}</Row>
                <Row label="External ID">{lead.external_id || "Not supplied"}</Row>
              </Group>
            </details>
          </aside>
        </div>
      </div>

      {/* ----------------------------------------------------- Qualification */}
      <div id="lead-panel-ai" className="v-panel" role="tabpanel" aria-labelledby="lead-tab-ai" tabIndex={0} hidden={shownSection !== "ai"}>
        <div className="v-stack">
          <section className="v-card v-card-pad">
            <h2 className="v-card-title">How this score was reached</h2>
            {qualification ? <ScoreStory qualification={qualification} /> : <p className="v-card-sub">This lead hasn’t been qualified yet.</p>}
          </section>

          {route && (
            <section className="v-card v-card-pad">
              <h2 className="v-card-title">What to do next</h2>
              <p className="v-card-sub">{queueLabel(route.assigned_queue)}{route.assigned_rep_name ? ` · ${route.assigned_rep_name}` : ""} · {route.routing_reason}</p>
              <div className="v-nextstep">
                <span>Recommended now</span>
                <b>{route.action_plan.immediate_action}</b>
                <p>{route.action_plan.next_step_recommendation}</p>
              </div>
              <div className="v-twocol">
                <div>
                  <h3 className="v-mini-title">Talking points</h3>
                  {talkingPoints.length
                    ? <ul className="v-bullets">{talkingPoints.map((item) => <li key={item}>{item}</li>)}</ul>
                    : <p className="v-card-sub">No talking points available yet.</p>}
                </div>
                <div>
                  <h3 className="v-mini-title">Still unknown</h3>
                  {route.action_plan.unanswered_questions.length ? (
                    <ul className="v-bullets">{route.action_plan.unanswered_questions.map((item) => <li key={item}>{item}</li>)}</ul>
                  ) : <p className="v-card-sub">No open qualification questions.</p>}
                </div>
              </div>
            </section>
          )}

          <div className="v-twocol v-twocol-cards">
            <Group title={analysis ? `${titleize(analysis.intent_level)} intent` : "Intent"} footer={analysis ? `${Math.round(analysis.overall_confidence * 100)}% confidence · stated means the lead said it; inferred means Corral read it from context.` : undefined}>
              {analysis ? (
                <>
                  <Row label="Urgency" detail={analysis.context.urgency.information_kind}>{analysis.context.urgency.value || "Unknown"}</Row>
                  <Row label="Timeline" detail={analysis.context.explicit_timeline.information_kind}>{analysis.context.explicit_timeline.value || "Unknown"}</Row>
                  <Row label="Budget" detail={analysis.context.budget_information.information_kind}>{analysis.context.budget_information.value || "Unknown"}</Row>
                  <Row label="Buying stage" detail={analysis.context.buying_stage.information_kind}>{analysis.context.buying_stage.value || "Unknown"}</Row>
                </>
              ) : <Row label="Analysis">Unavailable</Row>}
            </Group>
            <Group title="Ideal customer fit" footer={qualification?.icp_source === "not_configured" ? "ICP fit and ICP exclusions are skipped until this workspace publishes its own ICP." : analysis?.icp_evaluation.profile_name}>
              {qualification?.icp_source === "not_configured" || !analysis ? (
                <Row label="ICP">Not published</Row>
              ) : (
                ([
                  ["Industry", analysis.icp_evaluation.industry_fit],
                  ["Company size", analysis.icp_evaluation.company_size_fit],
                  ["Geography", analysis.icp_evaluation.geography_fit],
                  ["Buyer role", analysis.icp_evaluation.buyer_role_fit],
                  ["Budget", analysis.icp_evaluation.budget_fit],
                  ["Pain relevance", analysis.icp_evaluation.pain_point_relevance],
                ] as const).map(([name, criterion]) => (
                  <Row key={name} label={name} detail={criterion.rationale}>
                    <span className="v-fit" data-status={criterion.status}>{titleize(criterion.status)}</span>
                  </Row>
                ))
              )}
            </Group>
          </div>

          <Momentum lead={lead} />
          <div className="v-legacy-block"><AiStageAssessment analysis={analysis} /></div>
          <Delivery
            lead={lead}
            canCrmSync={canCrmSync}
            canManageRouting={canManageRouting}
            canEdit={canEdit}
            canViewEmail={canViewEmail}
            onComposeEmail={() => startEmail()}
            onUpdated={onUpdated}
          />
        </div>
      </div>

      {/* --------------------------------------------------------- Follow-up */}
      <div id="lead-panel-follow-ups" className="v-panel" role="tabpanel" aria-labelledby="lead-tab-follow-ups" tabIndex={0} hidden={shownSection !== "follow-ups"}>
        <div className="v-stack">
          {route && (
            <section className="v-card v-card-pad">
              <h2 className="v-card-title">Follow-up plan</h2>
              <p className="v-card-sub">Goal: {route.action_plan.conversion_goal || route.action_plan.next_step_recommendation}</p>
              <ol className="v-steps">
                {(route.action_plan.follow_up_strategy?.length ? route.action_plan.follow_up_strategy : [route.action_plan.immediate_action, route.action_plan.next_step_recommendation]).map((step, index) => (
                  <li key={`${index}-${step}`}>{step}</li>
                ))}
              </ol>
              {route.manual_review && <Banner tone="attention" title="Validate the analysis before contacting this lead." />}
            </section>
          )}
          <FollowUpComposer lead={lead} canEdit={canEdit} />
          <div className="v-legacy-block">
            <LeadMeetingPanel lead={lead} canEdit={canEdit} canViewEmails={canViewEmail} refreshToken={meetingRefresh} onDraftCreated={() => onSectionChange("email")} />
          </div>
        </div>
      </div>

      {/* ------------------------------------------------------------- Email */}
      <section id="lead-panel-email" className="v-panel" role="tabpanel" aria-labelledby="lead-tab-email" tabIndex={0} hidden={shownSection !== "email"}>
        <div className="v-stack">
          {canEdit && <FollowUpComposer lead={lead} canEdit={canEdit} seed={compose} title={`Write to ${lead.full_name || title}`} />}
          <div className="v-legacy-block">
            <LeadEmailPanel
              lead={lead}
              active={shownSection === "email"}
              canView={canViewEmail}
              canSend={canEdit}
              onAskAgent={() => onSectionChange("agent")}
              onConversationSynced={synced}
            />
          </div>
        </div>
      </section>

      {/* ---------------------------------------------- Progress & History */}
      <ProgressAndHistory lead={lead} canEdit={canEdit} refreshToken={activityRefresh} activeSection={shownSection} onConversationSynced={synced} />

      {/* --------------------------------------------------------- Assistant */}
      <section id="lead-panel-agent" className="v-panel v-legacy-block" role="tabpanel" aria-labelledby="lead-tab-agent" tabIndex={0} hidden={shownSection !== "agent"}>
        <LeadAssistantPanel
          lead={lead}
          canEdit={canEdit}
          canCrmSync={canCrmSync}
          canViewEmail={canViewEmail}
          canUseAgent={canUseAgent}
          dialerPhoneConnectionId={dialerPhoneConnectionId}
          onNavigateToDialer={onNavigateToDialer}
          onOpenLead={(leadId) => leadAction(leadId, "open")}
          onUpdated={(updated) => onUpdated?.(updated)}
          onDeleted={(leadId) => onDeleted?.(leadId)}
          onSectionChange={onSectionChange}
        />
      </section>
      {move && (
        <StageMoveSheet
          view={dealView}
          target={move}
          onClose={() => setMove(null)}
          onMoved={(fresh, message) => {
            setMove(null);
            setNotice(message);
            if (fresh) onUpdated?.(fresh);
            synced();
          }}
        />
      )}
    </article>
  );
}

function manualReviewReasons(lead: Lead) {
  const route = lead.routing;
  const analysis = lead.analysis;
  const generic = "Lead matches an enabled manual-review condition.";
  const legacy = "Manual-review precedence: analysis uncertainty or provider failure requires human review.";
  const routed = route?.manual_review ? route.routing_reason.replace(" No rep assignment is applicable.", "").trim() : "";
  const fromAnalysis = !lead.review_reasons?.length && analysis?.error_code === "manual_review_conditions" ? analysis.error_message?.trim() || "" : "";
  return [
    ...(lead.review_reasons ?? []),
    routed && routed !== generic && !routed.startsWith(legacy) ? routed : "",
    fromAnalysis && !routed.startsWith(fromAnalysis) ? fromAnalysis : "",
  ].filter((reason, index, all) => Boolean(reason) && all.indexOf(reason) === index);
}

/* ------------------------------------------------------------- Progress */

/** Where the lead is in Corral's pipeline — a checklist, read top to bottom. */
function ProgressList({ lead }: { lead: Lead }) {
  const stages = [
    ["Received", true],
    ["Analyzed", Boolean(lead.analysis)],
    ["Qualified", Boolean(lead.qualification)],
    ["Routed", Boolean(lead.routing)],
    ["Team notified", Boolean(lead.routing?.notification_state === "sent")],
    ["Confirmation emailed", lead.confirmation_email?.status === "sent"],
    ["In CRM", lead.crm_sync?.status === "synced"],
  ] as const;
  const done = stages.filter(([, complete]) => complete).length;
  return (
    <section className="v-group">
      <header className="v-group-head"><h3>Progress</h3><span className="v-meta num">{done} of {stages.length}</span></header>
      <ol className="v-checklist v-group-body" aria-label={`Decision trail: ${done} of ${stages.length} stages recorded`}>
        {stages.map(([label, complete]) => (
          <li key={label} data-done={complete || undefined}>
            <span className="v-checkmark" aria-hidden="true">{complete && <Icon name="check" size={12} strokeWidth={2.2} />}</span>
            <span>{label}</span>
            <span className="v-sr">{complete ? "recorded" : "not recorded"}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}

/* ---------------------------------------------------------- Score story */

/** Explains the recorded score exactly as the scoring service produced it. Never recalculates. */
export function ScoreStory({ qualification }: { qualification: Qualification }) {
  const totals = componentTotals(qualification);
  const earned = totals.reduce((sum, item) => sum + item.points, 0);
  const available = totals.reduce((sum, item) => sum + item.max, 0);
  const icpActive = qualification.icp_source !== "not_configured";
  const extra = qualification.rationale.filter((line) => !(qualification.score_breakdown ?? []).some((item) => line.includes(item.reason)));
  return (
    <div className="v-score-story cx-score-explainer">
      <div className="v-score-hero">
        <ScoreRing score={qualification.total_score} band={qualification.classification} size={88} label={`Score ${qualification.total_score} of 100`} />
        <div>
          <p className="v-score-sentence">
            {qualification.hard_disqualified ? (
              <>Disqualified by a hard exclusion{qualification.disqualifier_code ? ` (${titleize(qualification.disqualifier_code)})` : ""}.</>
            ) : available > 0 ? (
              <>Earned <b className="num">{earned}</b> of <b className="num">{available}</b> available points{available !== 100 ? ", scaled to 100" : ""}.</>
            ) : (
              <>Score recorded without a point breakdown.</>
            )}
          </p>
          <p className="v-card-sub">
            {icpActive ? (
              <>Scored on ICP fit, intent & urgency and engagement · ICP {qualification.icp_profile_version || "published"}</>
            ) : (
              <>Scored on intent & urgency and engagement. ICP fit is not scored until this workspace publishes its own ICP.</>
            )}
          </p>
        </div>
      </div>
      <ul className="v-components">
        {totals.map((item) => (
          <li key={item.key}>
            <details>
              <summary>
                <span className="v-component-label">{item.label}</span>
                <span className="v-meter" aria-hidden="true"><i style={{ transform: `scaleX(${item.max ? item.points / item.max : 0})` }} /></span>
                <span className="v-component-points num">{item.points}<small> / {item.max}</small></span>
                <Icon name="chevronDown" size={14} className="v-disclose" />
              </summary>
              {item.lines.length > 0 && (
                <ul className="v-factors">
                  {item.lines.map((line) => (
                    <li key={line.key} data-earned={line.points > 0 || undefined}>
                      <span className="v-factor-points num">{line.points}/{line.max_points}</span>
                      <span>
                        <b>{titleize(line.key)}</b>
                        {line.reason}
                        {line.evidence.length > 0 && <q>{line.evidence.slice(0, 2).join(" · ")}</q>}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </details>
          </li>
        ))}
        {!icpActive && (
          <li className="v-component-off">
            <span className="v-component-label">ICP fit</span>
            <span>Not scored · no published workspace ICP</span>
          </li>
        )}
      </ul>
      {extra.length > 0 && (
        <details className="v-disclosure">
          <summary>Full scoring rationale</summary>
          <ul className="v-bullets">{qualification.rationale.map((line) => <li key={line}>{line}</li>)}</ul>
        </details>
      )}
    </div>
  );
}

/* --------------------------------------------------------------- Momentum */

function dedupeEvidence(items: LeadMomentum["evidence"]) {
  const seen = new Map<string, { item: LeadMomentum["evidence"][number]; count: number }>();
  for (const item of items) {
    const key = `${item.source}\u0000${item.excerpt.trim().toLowerCase()}`;
    const existing = seen.get(key);
    if (existing) existing.count += 1;
    else seen.set(key, { item, count: 1 });
  }
  return [...seen.values()];
}

function Momentum({ lead }: { lead: Lead }) {
  const [momentum, setMomentum] = useState<LeadMomentum | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    getLeadMomentum(lead.id, controller.signal)
      .then((value) => { if (!controller.signal.aborted) setMomentum(value); })
      .catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load momentum evidence."); });
    return () => controller.abort();
  }, [lead.id]);
  if (error) return <Banner tone="neutral" title="Momentum evidence is unavailable right now">{error}</Banner>;
  if (!momentum) return <section className="v-card v-card-pad" aria-busy="true"><h2 className="v-card-title">Momentum</h2><i className="v-skel" style={{ width: "40%", marginTop: 14 }} /></section>;
  const delta = momentum.score_delta === null ? "First assessment" : `${momentum.score_delta >= 0 ? "+" : ""}${momentum.score_delta} since last check`;
  const evidence = dedupeEvidence(momentum.evidence);
  return (
    <section className="v-card v-card-pad">
      <h2 className="v-card-title">Momentum</h2>
      <p className="v-card-sub">What changed, from the evidence Corral has seen.</p>
      <div className="v-stats">
        <div><span>Temperature</span><b>{titleize(momentum.state)}</b><small>{titleize(momentum.classification)} · {momentum.score}/100</small></div>
        <div><span>Movement</span><b>{delta}</b><small>{momentum.previous_score === null ? "No earlier score" : `${momentum.previous_score} → ${momentum.score}`}</small></div>
        <div><span>Freshness</span><b>{momentum.freshness_hours < 1 ? "Just updated" : `${momentum.freshness_hours}h ago`}</b><small>{momentum.observed_signals} verified signal{momentum.observed_signals === 1 ? "" : "s"}</small></div>
      </div>
      <div className="v-nextstep">
        <span>Suggested next step for a person</span>
        <b>{momentum.next_human_action}</b>
        <p>A recommendation only. Corral won’t send or reroute anything on its own.</p>
      </div>
      <div className="v-twocol">
        <div>
          <h3 className="v-mini-title">Evidence</h3>
          {evidence.length ? (
            <ul className="v-evidence">
              {evidence.map(({ item, count }, index) => (
                <li key={`${item.source}-${index}`}>
                  <span className="v-meta">{titleize(item.source)}{count > 1 ? ` · seen ${count}×` : ""} · {item.observed_at ? new Date(item.observed_at).toLocaleString() : "Original assessment"}</span>
                  <p>{item.excerpt}</p>
                </li>
              ))}
            </ul>
          ) : <p className="v-card-sub">No scored evidence yet. Record verified progress when something new happens.</p>}
        </div>
        <div>
          <h3 className="v-mini-title">Deliveries</h3>
          <ul className="v-evidence">
            {momentum.delivery.map((item) => (
              <li key={item.destination} data-attention={item.needs_attention || undefined}>
                <span className="v-meta">{item.destination}{item.attempted_at ? ` · ${new Date(item.attempted_at).toLocaleString()}` : ""}</span>
                <p><b>{titleize(item.status)}</b> — {item.detail}</p>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}

/* --------------------------------------------------------------- Delivery */

function Delivery({
  lead,
  canCrmSync,
  canManageRouting,
  canEdit,
  canViewEmail,
  onComposeEmail,
  onUpdated,
}: {
  lead: Lead;
  canCrmSync: boolean;
  canManageRouting: boolean;
  canEdit: boolean;
  canViewEmail: boolean;
  onComposeEmail: () => void;
  onUpdated?: (lead: Lead) => void;
}) {
  const [providers, setProviders] = useState<string[]>([]);
  const [provider, setProvider] = useState(lead.crm_sync?.provider || "");
  const [sending, setSending] = useState(false);
  const [retryingNotification, setRetryingNotification] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [result, setResult] = useState<CrmSync | null>(null);
  useEffect(() => {
    let active = true;
    listIntegrations()
      .then((cards) => {
        if (!active) return;
        const verified = cards
          .filter((card) => card.category.toLowerCase() === "crm" && card.directionality.manual_outbound && card.connection?.is_active && card.connection.verification_status === "verified")
          .map((card) => card.provider);
        setProviders(verified);
        setProvider((current) => (verified.includes(lead.crm_sync?.provider || "") ? lead.crm_sync!.provider : verified.includes(current) ? current : verified.length === 1 ? verified[0] : ""));
      })
      .catch(() => { if (active) setProviders([]); });
    return () => { active = false; };
  }, [lead.id, lead.crm_sync?.provider]);
  const route = lead.routing;
  const crm = result?.lead_id === lead.id ? result : lead.crm_sync;
  const notificationRetryable = Boolean(route && ["failed", "retryable"].includes(route.notification_state));
  const notificationProvider = route?.notifications[0]?.provider || "Team notification";
  const confirmationSent = lead.confirmation_email?.status === "sent";
  const classification = lead.qualification?.classification.toLowerCase();
  const disqualified = Boolean(lead.qualification?.hard_disqualified)
    || ["disqualify", "disqualified", "hard_disqualified"].includes(classification || "");
  const reviewBlocked = Boolean(route?.manual_review || lead.analysis?.requires_manual_review);
  const emailBlockReason = disqualified
    ? "This lead is disqualified. Review its qualification before reaching out."
    : reviewBlocked
      ? "This lead needs review before outreach."
      : !lead.email?.trim()
        ? "Add an email address to this lead before composing."
        : null;
  const retryNotification = async () => {
    setRetryingNotification(true);
    setNotice(null);
    try {
      const notification = await retryLeadNotification(lead.id);
      setNotice(notification.status === "sent" ? "Team notification sent." : `Team notification ${titleize(notification.status)}.`);
      try {
        onUpdated?.(await getLead(lead.id));
      } catch {
        setNotice("Team notification retry completed. Refresh the lead to see its latest state.");
      }
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Team notification retry failed.");
    } finally {
      setRetryingNotification(false);
    }
  };
  const send = async () => {
    setSending(true);
    setNotice(null);
    try {
      const outcome = await sendLeadToCrm(lead.id, provider);
      setResult(outcome);
      setNotice(outcome.status === "synced" ? `${providerLabel(provider)} updated because you asked.` : `${providerLabel(provider)} write ${titleize(outcome.status)}.`);
      try { onUpdated?.(await getLead(lead.id)); }
      catch { setNotice(`${providerLabel(provider)} write ${titleize(outcome.status)}. Refresh the lead to see the latest state.`); }
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "CRM write failed");
    } finally {
      setSending(false);
    }
  };
  const label = sending
    ? "Sending…"
    : crm?.provider === provider && crm.status === "failed"
      ? `Retry ${providerLabel(provider)}`
      : crm?.provider === provider && crm.status === "synced"
        ? `Update ${providerLabel(provider)}`
        : `Send to ${providerLabel(provider || "CRM")}`;
  return (
    <Group
      title="Deliveries"
      footer="Use the row actions to retry deliveries or open a reviewed email draft. CRM writes only happen when you request them."
    >
      <Row icon="bell" label="Team notification" detail={notificationProvider}>
        <span className="v-delivery-row-value">
          <span>{route ? titleize(route.notification_state) : "Pending"}</span>
          {canManageRouting && notificationRetryable && (
            <Button kind="tinted" size="sm" disabled={retryingNotification} onClick={() => void retryNotification()}>
              {retryingNotification ? "Retrying…" : "Retry"}
            </Button>
          )}
        </span>
      </Row>
      <Row
        icon="mail"
        label="Confirmation email"
        detail={lead.confirmation_email?.error_message || lead.confirmation_email?.recipient || (lead.email?.trim() ? "Needs a verified Gmail connection" : "No email address available")}
      >
        <span className="v-delivery-row-value">
          <span>{lead.confirmation_email ? titleize(lead.confirmation_email.status) : "Not attempted"}</span>
          {canEdit && canViewEmail && !confirmationSent && (
            <Button
              kind="tinted"
              size="sm"
              disabled={Boolean(emailBlockReason)}
              title={emailBlockReason || "Open a reviewed email draft"}
              onClick={onComposeEmail}
            >
              {reviewBlocked || disqualified ? "Review first" : "Compose"}
            </Button>
          )}
        </span>
      </Row>
      <Row icon="share" label="CRM" detail={crm?.last_error ? `${crm.error_category || "Delivery issue"}: ${crm.last_error}` : crm?.provider_entity_ids.contact_id || "No provider record ID"}>
        <span className="v-delivery-row-value">
          <span>{crm ? `${providerLabel(crm.provider)} · ${titleize(crm.status)}` : "Not synchronized"}</span>
          {canCrmSync && providers.length > 1 && (
            <select className="v-popup" aria-label="CRM provider" value={provider} onChange={(event) => setProvider(event.target.value)}>
              <option value="">Choose a CRM</option>
              {providers.map((item) => <option key={item} value={item}>{providerLabel(item)}</option>)}
            </select>
          )}
          {canCrmSync && providers.length > 0 && (
            <Button kind="tinted" size="sm" disabled={sending || !providers.includes(provider)} onClick={() => void send()}>
              {label}
            </Button>
          )}
        </span>
      </Row>
      {notice && <div className="v-row-note" role="status">{notice}</div>}
    </Group>
  );
}

/* -------------------------------------------------------- Follow-up email */

function FollowUpComposer({
  lead,
  canEdit,
  seed = null,
  title = "Personal follow-up",
}: {
  lead: Lead;
  canEdit: boolean;
  seed?: { subject?: string; body?: string; quoteId?: string; nonce: number } | null;
  title?: string;
}) {
  const [accounts, setAccounts] = useState<ProviderConnection[]>([]);
  const [open, setOpen] = useState(false);
  const [revision, setRevision] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ subject?: string; body?: string; quoteId?: string }>({});
  useEffect(() => {
    if (!seed) return;
    setDraft({ subject: seed.subject, body: seed.body, quoteId: seed.quoteId });
    setNotice(null);
    setOpen(true);
    setRevision((value) => value + 1);
  }, [seed]);
  useEffect(() => {
    if (!canEdit) {
      setAccounts([]);
      return;
    }
    let active = true;
    listIntegrations()
      .then((cards) => {
        if (!active) return;
        const gmail = cards.find((card) => card.provider === "gmail");
        setAccounts(gmail?.connections.length ? gmail.connections : gmail?.connection ? [gmail.connection] : []);
      })
      .catch((error) => { if (active) setNotice(error instanceof Error ? error.message : "Gmail accounts couldn’t be loaded."); });
    return () => { active = false; };
  }, [canEdit, lead.id]);
  const classification = lead.qualification?.classification.toLowerCase();
  const disqualified = Boolean(lead.qualification?.hard_disqualified) || ["disqualify", "disqualified", "hard_disqualified"].includes(classification || "");
  const needsReview = Boolean(lead.routing?.manual_review || lead.analysis?.requires_manual_review);
  const blocked = disqualified
    ? "This lead is disqualified. Review the evidence and update its qualification before reaching out."
    : needsReview
      ? "This lead needs a person to review it first. Record verified context in Progress, then re-check it."
      : null;
  return (
    <section className="v-card v-card-pad">
      <div className="v-card-headrow">
        <div>
          <h2 className="v-card-title">{title}</h2>
          <p className="v-card-sub">Write from your connected Gmail. Nothing sends until you press Send; sent mail is kept in this lead’s history.</p>
        </div>
        {canEdit && !open && (
          <Button kind="tinted" icon="send" disabled={Boolean(blocked)} onClick={() => { setNotice(null); setDraft({}); setOpen(true); setRevision((value) => value + 1); }}>
            Compose
          </Button>
        )}
      </div>
      {blocked && <Banner tone="attention" title={disqualified ? "Outreach paused · disqualified" : "Outreach paused · review first"}>{blocked}</Banner>}
      {open && !blocked && (
        <div className="v-legacy-block v-composer">
          <EmailComposer
            key={revision}
            accounts={accounts}
            canSend={canEdit}
            leadId={lead.id}
            seed={{ recipient: lead.email?.trim() ?? "", purpose: "follow_up", subject: draft.subject, body: draft.body }}
            onClose={() => setOpen(false)}
            onSent={() => {
              setOpen(false); setNotice("Email sent. You’ll find it in this lead’s email history.");
              if (draft.quoteId && getDeal(lead.id).quotes.find((quote) => quote.id === draft.quoteId)?.status === "draft") {
                dealActions.setQuoteStatus(lead.id, draft.quoteId, "sent");
                void flushDealChanges().catch(() => setNotice("Email sent, but the quote status could not be saved. Check the deal save banner before retrying; do not resend the email."));
              }
            }}
          />
        </div>
      )}
      {notice && <Banner tone="tint" title={notice} />}
    </section>
  );
}

/* ------------------------------------------------- Progress & history */

const STAGES: Array<{ value: string; label: string }> = [
  { value: "contacted", label: "Contacted" },
  { value: "responded", label: "Responded" },
  { value: "meeting_scheduled", label: "Meeting scheduled" },
  { value: "proposal_sent", label: "Proposal sent" },
  { value: "converted", label: "Won — Project or Sale" },
  { value: "lost", label: "Closed — lost" },
];

function ProgressAndHistory({
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
  const [sendDirectMessage, setSendDirectMessage] = useState(lead.outcome?.send_direct_message ?? false);
  const [members, setMembers] = useState<Awaited<ReturnType<typeof getLeadClosingOptions>>>([]);
  const [outcome, setOutcome] = useState<LeadOutcome | null>(lead.outcome ?? null);
  useEffect(() => {
    setOutcome(lead.outcome ?? null);
    setSendDirectMessage(lead.outcome?.send_direct_message ?? false);
  }, [lead.outcome]);
  useEffect(() => {
    if (!canEdit) return;
    const controller = new AbortController();
    getLeadClosingOptions(controller.signal)
      .then((rows) => {
        if (controller.signal.aborted) return;
        setMembers(rows);
        setCreditedId((current) => (rows.some((member) => member.user_id === current) ? current : ""));
      })
      .catch((error) => { if (!controller.signal.aborted) setNotice(error instanceof Error ? error.message : "Unable to load team members."); });
    return () => controller.abort();
  }, [lead.id, canEdit]);
  useEffect(() => {
    const controller = new AbortController();
    setEventsLoading(true);
    setEventsError(null);
    getLeadPipelineHistory(lead.id, controller.signal)
      .then((rows) => {
        if (controller.signal.aborted) return;
        setEvents(rows);
        setEventsLoading(false);
        const latest = [...rows].reverse().find((event) => event.stage === "manual_progress");
        if (latest && typeof latest.details.stage === "string") setStage(latest.details.stage);
      })
      .catch((error) => {
        if (controller.signal.aborted) return;
        setEventsError(error instanceof Error ? error.message : "Unable to load the lead history.");
        setEventsLoading(false);
      });
    return () => controller.abort();
  }, [lead.id, refreshToken, historyRevision]);

  const syncConversation = async () => {
    setSyncBusy(true);
    setNotice(null);
    try {
      const result = await syncLeadGmailConversation(lead.id);
      onConversationSynced();
      const summary = result.messages_added
        ? `Synced ${result.messages_added} new Gmail messages across ${result.threads_checked} threads. Corral re-checked this lead: ${titleize(result.classification)} (${result.score}/100).${result.truncated ? " More than 200 threads were found; the sync was capped for safety." : ""}`
        : `No new Gmail messages across ${result.threads_checked} threads. Current assessment: ${titleize(result.classification)} (${result.score}/100).`;
      const unreadable = result.threads_unreadable
        ? ` Google denied access to ${result.threads_unreadable} thread${result.threads_unreadable === 1 ? "" : "s"}; check that the connected mailbox can open ${result.threads_unreadable === 1 ? "it" : "them"}.`
        : "";
      setNotice(`${summary}${unreadable}`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Unable to sync Gmail conversation");
    } finally {
      setSyncBusy(false);
    }
  };
  const save = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setNotice(null);
    try {
      const closing = stage === "converted" || stage === "lost";
      const result = await updateLeadProgress(lead.id, {
        stage,
        note,
        ...(stage === "converted" ? { conversion_type: conversionType, closed_revenue: value === "" ? null : Number(value) } : {}),
        ...(closing ? { credited_user_id: creditedId || null } : {}),
        ...(stage === "converted" ? { send_direct_message: sendDirectMessage } : {}),
      });
      setNote("");
      if (closing) {
        setOutcome(result.outcome ?? null);
        setNotice(stage === "converted" ? `${titleize(conversionType)} win recorded in Corral. Qualification is unchanged; no CRM record was created.` : "Closed – lost recorded. Qualification is unchanged.");
      } else {
        setNotice(`Progress saved. Corral re-checked this lead: ${titleize(result.classification)} (${result.score}/100).`);
      }
      onConversationSynced();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Unable to update lead progress");
    } finally {
      setBusy(false);
    }
  };

  const progressEvents = events.filter((event) => event.stage === "manual_progress").reverse();
  const emailEvents = events.filter((event) => event.stage === "gmail_message").reverse();
  const meetingEvents = events.filter((event) => event.stage === "meeting_scheduled").reverse();
  type Entry = { id: string; icon: "note" | "mail" | "calendar"; title: string; when: string; body?: string; meta?: string; link?: string };
  const timeline: Entry[] = [
    ...progressEvents.slice(0, 5).map((event) => ({
      id: event.id,
      icon: "note" as const,
      title: titleize(String(event.details.stage || event.status)),
      when: event.created_at,
      body: String(event.details.note || ""),
      meta: `Assessment after update: ${titleize(String((event.details.qualification as { classification?: string } | undefined)?.classification || "pending"))} · ${String((event.details.qualification as { score?: number } | undefined)?.score ?? "—")}/100`,
    })),
    ...emailEvents.slice(0, 10).map((event) => ({
      id: event.id,
      icon: "mail" as const,
      title: `${event.details.direction === "outbound" ? "You sent" : "Lead sent"}: ${String(event.details.subject || "(no subject)")}`,
      when: event.created_at,
      body: String(event.details.body || ""),
      meta: `${String(event.details.sender || "")} → ${String(event.details.recipients || "")}`,
    })),
    ...meetingEvents.slice(0, 10).map((event) => ({
      id: event.id,
      icon: "calendar" as const,
      title: `${String(event.details.title || "Meeting")} · ${titleize(String(event.details.provider || ""))}`,
      when: String(event.details.start_at || "") || event.created_at,
      body: "Scheduled from the Gmail conversation. The connected workspace account hosts it.",
      link: typeof event.details.event_url === "string" ? event.details.event_url : undefined,
    })),
  ].sort((a, b) => Date.parse(b.when) - Date.parse(a.when));

  const closing = stage === "converted" || stage === "lost";
  return (
    <>
      <section id="lead-panel-closing" className="v-panel" role="tabpanel" aria-labelledby="lead-tab-history" tabIndex={0} hidden={activeSection !== "history"}>
        <div className="v-stack">
          {outcome && (
            <OutcomeReceipt
              outcome={outcome}
              canEdit={canEdit}
              busy={busy}
              onRetry={async () => {
                setBusy(true);
                setNotice(null);
                try { setOutcome(await retryLeadCongratulations(lead.id)); }
                catch (error) { setNotice(error instanceof Error ? error.message : "Unable to retry congratulations."); }
                finally { setBusy(false); }
              }}
            />
          )}
          {canEdit ? (
            <form className="v-card v-card-pad v-form" onSubmit={(event) => void save(event)}>
              <div className="v-card-headrow">
                <div>
                  <h2 className="v-card-title">Record progress</h2>
                  <p className="v-card-sub">Each update needs a short, verified comment. Corral re-checks the lead afterwards; closing a lead doesn’t re-run AI or create a CRM record.</p>
                </div>
                <Button size="sm" disabled={syncBusy || busy} onClick={() => void syncConversation()} icon="refresh">
                  {syncBusy ? "Syncing Gmail…" : "Sync Gmail & Re-check"}
                </Button>
              </div>
              <div className="v-stagepicker" role="radiogroup" aria-label="Manual progress or closing outcome">
                {STAGES.map((option) => (
                  <label key={option.value} className="v-stageopt" data-kind={option.value === "converted" ? "won" : option.value === "lost" ? "lost" : undefined}>
                    <input type="radio" name={`stage-${lead.id}`} value={option.value} checked={stage === option.value} onChange={() => setStage(option.value)} />
                    <span>{option.label}</span>
                  </label>
                ))}
              </div>
              {stage === "converted" && (
                <div className="v-form-grid">
                  <label className="v-field">
                    <span>Won as</span>
                    <select value={conversionType} onChange={(event) => setConversionType(event.target.value as "project" | "sale")}>
                      <option value="project">Project</option>
                      <option value="sale">Sale</option>
                    </select>
                  </label>
                  <label className="v-field">
                    <span>Value in USD (optional)</span>
                    <input type="number" min="0" step="0.01" max="999999999999.99" value={value} onChange={(event) => setValue(event.target.value)} placeholder="Leave blank if unknown" />
                  </label>
                </div>
              )}
              {closing && (
                <label className="v-field">
                  <span>Credit to</span>
                  <select value={creditedId} onChange={(event) => setCreditedId(event.target.value)}>
                    <option value="">Assigned member, if linked</option>
                    {members.map((member) => <option key={member.user_id} value={member.user_id}>{member.display_name}{!member.slack_linked ? " · Slack link needed" : ""}</option>)}
                  </select>
                  <small>{creditedId ? "This person receives analytics credit. A direct message goes out only if you turn it on below and their Slack ID is linked." : "Uses the assigned member if linked; otherwise the win is uncredited."}</small>
                </label>
              )}
              {stage === "converted" && (
                <label className="v-toggle-row">
                  <span>
                    <b>Send a private Slack congratulations</b>
                    <small>Off by default. Needs the member’s Slack ID and the optional im:write scope. There’s no channel fallback.</small>
                  </span>
                  <input type="checkbox" role="switch" className="v-switch" checked={sendDirectMessage} disabled={!creditedId} onChange={(event) => setSendDirectMessage(event.target.checked)} />
                </label>
              )}
              <label className="v-field">
                <span>Comment</span>
                <textarea value={note} onChange={(event) => setNote(event.target.value)} rows={3} minLength={3} maxLength={2000} placeholder="e.g. Replied asking for a proposal by Friday; timeline is now this quarter." required />
              </label>
              {outcome && ["closed_won", "closed_lost"].includes(outcome.pipeline_stage) && (
                <p className="v-hint">Choosing a closing stage corrects the existing outcome. Congratulations already sent won’t be sent again.</p>
              )}
              <div className="v-form-actions">
                <Button kind="filled" type="submit" disabled={busy || note.trim().length < 3}>
                  {busy ? "Saving…" : stage === "converted" ? `Record ${titleize(conversionType)} Win` : stage === "lost" ? "Record as Lost" : "Save and Re-check"}
                </Button>
              </div>
            </form>
          ) : (
            <div className="v-legacy-block"><AiStageAssessment analysis={lead.analysis} /></div>
          )}
          {notice && <Banner tone="tint" title={notice} />}
        </div>
      </section>

      <section id="lead-panel-history" className="v-panel v-panel-follow" role="region" aria-label="Lead history" hidden={activeSection !== "history"}>
        {eventsLoading && (
          <div className="v-card v-card-pad" aria-label="Loading lead history">
            <i className="v-skel" style={{ width: "50%" }} /><i className="v-skel" style={{ width: "70%", marginTop: 12 }} /><i className="v-skel" style={{ width: "40%", marginTop: 12 }} />
          </div>
        )}
        {eventsError && (
          <Banner tone="danger" title="History couldn’t be loaded" action={<Button size="sm" onClick={() => setHistoryRevision((revision) => revision + 1)}>Try Again</Button>}>{eventsError}</Banner>
        )}
        {!eventsLoading && !eventsError && timeline.length === 0 && (
          <div className="v-card"><EmptyState icon="clock" title="Nothing recorded yet">Progress updates, Gmail messages and scheduled meetings will appear here as they happen.</EmptyState></div>
        )}
        {timeline.length > 0 && (
          <ol className="v-timeline">
            {timeline.map((entry) => (
              <li key={entry.id}>
                <span className="v-timeline-icon"><Icon name={entry.icon} size={15} /></span>
                <div className="v-timeline-body">
                  <div className="v-timeline-head">
                    <b>{entry.title}</b>
                    <time dateTime={entry.when}>{new Date(entry.when).toLocaleString()}</time>
                  </div>
                  {entry.body && <p>{entry.body}</p>}
                  {entry.meta && <small>{entry.meta}</small>}
                  {entry.link && <a href={entry.link} target="_blank" rel="noreferrer">Open event <Icon name="arrowUpRight" size={12} /></a>}
                </div>
              </li>
            ))}
          </ol>
        )}
      </section>
    </>
  );
}

function OutcomeReceipt({ outcome, canEdit, busy, onRetry }: { outcome: LeadOutcome; canEdit: boolean; busy: boolean; onRetry: () => Promise<void> }) {
  const won = outcome.pipeline_stage === "closed_won";
  if (!won && outcome.pipeline_stage !== "closed_lost") return null;
  const retryable = outcome.notifications.some((delivery) => ["failed", "retryable", "not_configured"].includes(delivery.status));
  return (
    <section className="v-outcome" data-won={won || undefined} role="status">
      <span className="v-outcome-icon"><Icon name={won ? "check" : "close"} size={20} strokeWidth={2} /></span>
      <div>
        <h2>{won ? `Won — nice work, ${outcome.credited_name ?? "team"}` : "Closed — lost"}</h2>
        <p>
          {won ? `${titleize(outcome.conversion_type ?? "closed_won")} recorded` : "Lost outcome recorded"}
          {outcome.closed_revenue !== null && won ? ` · ${outcome.currency} ${outcome.closed_revenue.toLocaleString(undefined, { minimumFractionDigits: 2 })}` : won ? " · Value not recorded" : ""}
          {outcome.closed_at ? ` · ${new Date(outcome.closed_at).toLocaleDateString()}` : ""}
        </p>
        {won && outcome.notifications.length > 0 && (
          <ul className="v-outcome-deliveries">
            {outcome.notifications.map((delivery) => (
              <li key={delivery.destination}>
                <span>{delivery.destination === "channel" ? "Team channel" : "Direct message"}</span>
                <b>{titleize(delivery.status)}</b>
                {delivery.error_message && <small>{delivery.error_message}</small>}
              </li>
            ))}
          </ul>
        )}
        {won && retryable && canEdit && <Button size="sm" disabled={busy} onClick={() => void onRetry()}>{busy ? "Retrying…" : "Retry Unsent Congratulations"}</Button>}
      </div>
    </section>
  );
}
