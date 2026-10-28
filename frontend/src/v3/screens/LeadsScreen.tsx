import { memo, useEffect, useRef, useState, type FormEvent } from "react";
import {
  bulkDeleteLeads,
  bulkSendLeadsToCrm,
  createLead,
  exportLeads,
  LEAD_EXPORT_COLUMNS,
  listIntegrations,
  listLeadAssignmentOptions,
  listLeads,
  updateLeadAssignment,
  updateLeadGenerated,
  type LeadExportColumn,
} from "../../api";
import { AutomationEnrollmentDialog } from "../../components/AutomationEnrollmentDialog";
import type { LeadDetailSection } from "../../leadSections";
import type { Lead, LeadAssignmentOption, LeadPayload } from "../../types";
import { formatMoney, stageInfo, useDeals, viewDeal } from "../deals/dealStore";
import { leadAction, NEW_LEAD_EVENT } from "../shell/events";
import { Icon } from "../ui/Icon";
import {
  BandBadge,
  Banner,
  Button,
  EmptyState,
  formatShortDate,
  IconButton,
  Monogram,
  PageHeader,
  ScoreRing,
  Segmented,
  Sheet,
  titleize,
} from "../ui/primitives";
import { LeadRecord, type LeadIntent } from "./LeadRecord";
import { providerLabel, queueLabel, sourceLabel } from "./leadFormat";

const LEAD_PAGE_SIZE = 50;

const emptyLead: LeadPayload = {
  source: "website",
  source_detail: "manual-control-center",
  full_name: "",
  email: "",
  phone: "",
  job_title: "",
  company_name: "",
  industry: "",
  employee_count: null,
  annual_revenue: null,
  geography: "",
  budget: null,
  product_or_service: "",
  message: "",
};

type Filters = { search: string; band: string; source: string; dateFrom: string; dateTo: string };
const emptyFilters = (): Filters => ({ search: "", band: "all", source: "all", dateFrom: "", dateTo: "" });

const BANDS = ["hot", "warm", "needs_qualifying", "nurture", "disqualify"];
const BAND_NAMES: Record<string, string> = { hot: "Hot", warm: "Warm", needs_qualifying: "Needs qualifying", nurture: "Nurture", disqualify: "Disqualified" };
const SOURCES = ["email", "website", "offline", "unknown", "purchased_list", "web", "phone_inquiry", "partner_referral", "clutch", "salesforce", "hubspot", "gohighlevel"];
const crmOrigin = (lead: Lead) => {
  const match = lead.source_detail?.match(/^crm-import:(salesforce|hubspot|gohighlevel):/i);
  return match ? providerLabel(match[1].toLowerCase()) : null;
};
/** Lenses show the same leads through the columns each part of the job needs. */
type Lens = "overview" | "ai" | "follow-ups" | "email" | "closing" | "history" | "agent";
const LENSES: Array<{ value: Lens; label: string }> = [
  { value: "overview", label: "Owner & source" },
  { value: "ai", label: "Qualification" },
  { value: "follow-ups", label: "Follow-up" },
  { value: "email", label: "Email" },
  { value: "closing", label: "Outcome" },
  { value: "history", label: "Delivery" },
];
const lensOf = (section: LeadDetailSection): Lens => (LENSES.some((lens) => lens.value === section) ? (section as Lens) : "overview");
const readable = (value?: string | null) => (value ? titleize(value) : "Not recorded");
const dateOnly = (value?: string | null) => (value && Number.isFinite(Date.parse(value)) ? formatShortDate(value) : "Not recorded");
function lensColumns(lens: Lens): [string, string] {
  switch (lens) {
    case "ai": return ["Classification", "Service"];
    case "follow-ups": return ["Contact", "Intake confirmation"];
    case "email": return ["Contact", "Company"];
    case "closing": return ["Stage", "Recorded value"];
    case "history": return ["Intake confirmation", "CRM delivery"];
    default: return ["Source", "Owner"];
  }
}
function lensValues(lead: Lead, lens: Lens): [string, string] {
  switch (lens) {
    case "ai": return [lead.qualification ? readable(lead.qualification.classification) : "Not scored", lead.product_or_service || "Not recorded"];
    case "follow-ups": return [lead.email || "Not recorded", readable(lead.confirmation_email?.status)];
    case "email": return [lead.email || "Not recorded", lead.company_name || "Not recorded"];
    case "closing": return [
      readable(lead.outcome?.pipeline_stage),
      lead.outcome?.closed_revenue != null ? `${lead.outcome.currency} ${lead.outcome.closed_revenue.toLocaleString()}` : lead.outcome?.credited_name || "Not recorded",
    ];
    case "history": return [readable(lead.confirmation_email?.status), readable(lead.crm_sync?.status)];
    default: return ["", ""];
  }
}

type Props = {
  selected: Lead | null;
  activeSection: LeadDetailSection;
  onSectionChange: (section: LeadDetailSection) => void;
  isSectionPinned: boolean;
  onTogglePin: () => void;
  revision: number;
  onInspect: (id: string) => void;
  onClose: () => void;
  onCreated: (lead: Lead) => void;
  onUpdated: (lead: Lead) => void;
  onDeleted: (leadId: string) => void;
  newLeadIds: ReadonlySet<string>;
  canEdit?: boolean;
  canManageRouting?: boolean;
  canManageManualReview?: boolean;
  canExport?: boolean;
  canCrmSync?: boolean;
  canViewEmail?: boolean;
  canUseAgent?: boolean;
  dialerPhoneConnectionId?: string | null;
  onNavigateToDialer: () => void;
  canEnrollAutomations?: boolean;
  onOpenAutomations?: () => void;
  canCall?: boolean;
  recordedStages?: ReadonlyMap<string, string>;
  intent?: LeadIntent;
  embeddedPhone?: boolean;
  newLeadNonce?: number;
};

export function LeadsScreen({
  selected,
  activeSection,
  onSectionChange,
  isSectionPinned,
  onTogglePin,
  revision,
  onInspect,
  onClose,
  onCreated,
  onUpdated,
  onDeleted,
  newLeadIds,
  canEdit = true,
  canManageRouting = false,
  canManageManualReview = false,
  canExport = false,
  canCrmSync = false,
  canViewEmail = false,
  canUseAgent = false,
  dialerPhoneConnectionId = null,
  onNavigateToDialer,
  canEnrollAutomations = false,
  onOpenAutomations,
  canCall = false,
  recordedStages,
  intent = null,
  embeddedPhone = false,
  newLeadNonce = 0,
}: Props) {
  const deals = useDeals();
  const [draft, setDraft] = useState<Filters>(emptyFilters);
  const [applied, setApplied] = useState<Filters>(emptyFilters);
  const [lens, setLens] = useState<Lens>(() => lensOf(activeSection));
  const [rows, setRows] = useState<Lead[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const loadedOnce = useRef(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [showExport, setShowExport] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [controlError, setControlError] = useState<string | null>(null);
  const [crmProviders, setCrmProviders] = useState<string[]>([]);
  const [bulkProvider, setBulkProvider] = useState("");
  const [assignmentOptions, setAssignmentOptions] = useState<LeadAssignmentOption[]>([]);
  const [assignmentLoading, setAssignmentLoading] = useState(false);
  const [assignmentError, setAssignmentError] = useState<string | null>(null);
  const [savingAssignment, setSavingAssignment] = useState<Set<string>>(new Set());
  const [savingGenerated, setSavingGenerated] = useState<Set<string>>(new Set());
  const [enrollment, setEnrollment] = useState<{ kind: "sequence" | "playbook"; leadId: string; leadLabel: string } | null>(null);
  const [filtersOpen, setFiltersOpen] = useState(false);

  useEffect(() => {
    const open = () => { if (canEdit) setShowForm(true); };
    window.addEventListener(NEW_LEAD_EVENT, open);
    return () => window.removeEventListener(NEW_LEAD_EVENT, open);
  }, [canEdit]);
  useEffect(() => {
    if (newLeadNonce && canEdit) setShowForm(true);
  }, [newLeadNonce, canEdit]);

  // When a deep link names a section without a lead, show the list through that lens.
  useEffect(() => {
    if (!selected) setLens((current) => (activeSection === "overview" ? current : lensOf(activeSection)));
  }, [activeSection, selected]);

  useEffect(() => {
    if (!canManageRouting) {
      setAssignmentOptions([]);
      setAssignmentLoading(false);
      setAssignmentError(null);
      return;
    }
    let active = true;
    const controller = new AbortController();
    setAssignmentLoading(true);
    setAssignmentError(null);
    void listLeadAssignmentOptions(controller.signal)
      .then((options) => { if (active) setAssignmentOptions(options); })
      .catch((error) => {
        if (active && !controller.signal.aborted) setAssignmentError(error instanceof Error ? error.message : "Unable to load workspace representatives.");
      })
      .finally(() => { if (active) setAssignmentLoading(false); });
    return () => { active = false; controller.abort(); };
  }, [canManageRouting]);

  useEffect(() => {
    if (!canCrmSync) return;
    let active = true;
    void listIntegrations()
      .then((cards) => {
        if (!active) return;
        const providers = cards
          .filter((card) => card.category.toLowerCase() === "crm" && card.directionality.manual_outbound && card.connection?.is_active && card.connection.verification_status === "verified")
          .map((card) => card.provider);
        setCrmProviders(providers);
        setBulkProvider((current) => (providers.includes(current) ? current : providers.length === 1 ? providers[0] : ""));
      })
      .catch(() => { if (active) setCrmProviders([]); });
    return () => { active = false; };
  }, [canCrmSync]);

  useEffect(() => {
    setPage(0);
    setSelectedIds(new Set());
  }, [applied.search, applied.band, applied.source, applied.dateFrom, applied.dateTo]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") setRefreshKey((value) => value + 1);
    }, 5000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      if (!loadedOnce.current) setLoading(true);
      setLoadError(null);
      void listLeads(
        {
          limit: LEAD_PAGE_SIZE,
          offset: page * LEAD_PAGE_SIZE,
          search: applied.search.trim() || undefined,
          source: applied.source === "all" ? undefined : applied.source,
          qualification_band: applied.band === "all" ? undefined : applied.band,
          date_from: applied.dateFrom || undefined,
          date_to: applied.dateTo || undefined,
        },
        controller.signal,
      )
        .then((result) => {
          if (!active) return;
          setRows(result.items);
          setTotal(result.total);
          loadedOnce.current = true;
        })
        .catch((reason) => {
          if (active && !controller.signal.aborted) setLoadError(reason instanceof Error ? reason.message : "Unable to load leads");
        })
        .finally(() => { if (active) setLoading(false); });
    }, 180);
    return () => { active = false; window.clearTimeout(timer); controller.abort(); };
  }, [applied, page, refreshKey, revision]);

  // Filters apply as they change; typing is debounced so results never lag the controls.
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setApplied((current) =>
        current.search === draft.search && current.band === draft.band && current.source === draft.source && current.dateFrom === draft.dateFrom && current.dateTo === draft.dateTo
          ? current
          : { ...draft },
      );
    }, draft.search === applied.search ? 0 : 250);
    return () => window.clearTimeout(timer);
  }, [draft, applied.search]);

  const hasFilters = Boolean(applied.search.trim() || applied.band !== "all" || applied.source !== "all" || applied.dateFrom || applied.dateTo);
  const draftDiffers = JSON.stringify(draft) !== JSON.stringify(applied);
  const refinementCount = [draft.band !== "all", draft.source !== "all", Boolean(draft.dateFrom || draft.dateTo)].filter(Boolean).length;
  const clearFilters = () => {
    const empty = emptyFilters();
    setDraft(empty);
    setApplied(empty);
  };

  const visibleSelected = rows.filter((lead) => selectedIds.has(lead.id)).length;
  const toggleSelected = (id: string) =>
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const selectVisible = () =>
    setSelectedIds((current) => {
      const next = new Set(current);
      if (visibleSelected === rows.length) rows.forEach((lead) => next.delete(lead.id));
      else rows.forEach((lead) => next.add(lead.id));
      return next;
    });

  const updateRow = (lead: Lead) => {
    setRows((current) => current.map((item) => (item.id === lead.id ? lead : item)));
    onUpdated(lead);
  };
  const withSaving = (setter: typeof setSavingGenerated, id: string, on: boolean) =>
    setter((current) => {
      const next = new Set(current);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  const changeGenerated = async (leadId: string, isGenerated: boolean) => {
    setControlError(null);
    withSaving(setSavingGenerated, leadId, true);
    try { updateRow(await updateLeadGenerated(leadId, isGenerated)); }
    catch (error) { setControlError(error instanceof Error ? error.message : "Unable to update lead status."); }
    finally { withSaving(setSavingGenerated, leadId, false); }
  };
  const changeAssignment = async (leadId: string, repId: string | null) => {
    setControlError(null);
    withSaving(setSavingAssignment, leadId, true);
    try { updateRow(await updateLeadAssignment(leadId, repId)); }
    catch (error) { setControlError(error instanceof Error ? error.message : "Unable to update the assigned representative."); }
    finally { withSaving(setSavingAssignment, leadId, false); }
  };

  const deleteSelected = async () => {
    if (!selectedIds.size || !window.confirm(`Delete ${selectedIds.size} selected lead${selectedIds.size === 1 ? "" : "s"}? A later matching inquiry can be imported again.`)) return;
    try {
      const result = await bulkDeleteLeads([...selectedIds]);
      setSelectedIds(new Set());
      setNotice(`${result.deleted} lead${result.deleted === 1 ? "" : "s"} deleted. Later matching inquiries can be imported again.`);
      setRefreshKey((value) => value + 1);
      void onDeleted("bulk");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Unable to delete selected leads");
    }
  };
  const transferSelected = async () => {
    if (!window.confirm(`Transfer or update ${selectedIds.size} selected leads in ${providerLabel(bulkProvider)}?`)) return;
    try {
      const result = await bulkSendLeadsToCrm([...selectedIds], bulkProvider);
      const firstFailure = result.results.find((item) => item.status !== "synced" && item.error)?.error;
      setNotice(`${result.succeeded} ${providerLabel(bulkProvider)} transfers completed; ${result.failed} skipped or failed.${firstFailure ? ` ${firstFailure}` : ""}`);
      setRefreshKey((value) => value + 1);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Bulk CRM transfer failed");
    }
  };

  const pageCount = Math.max(1, Math.ceil(total / LEAD_PAGE_SIZE));
  const firstRecord = total === 0 ? 0 : page * LEAD_PAGE_SIZE + 1;
  const lastRecord = Math.min((page + 1) * LEAD_PAGE_SIZE, total);
  const [colA, colB] = lensColumns(lens);

  const enrollmentDialog = enrollment && (
    <AutomationEnrollmentDialog
      key={`${enrollment.kind}-${enrollment.leadId}`}
      kind={enrollment.kind}
      fixedLeadIds={[enrollment.leadId]}
      fixedLeadLabel={enrollment.leadLabel}
      onClose={() => setEnrollment(null)}
      onOpenAutomations={onOpenAutomations}
    />
  );

  if (selected) {
    return (
      <div className="v-split" data-lead-page={activeSection}>
        {rows.length > 1 && (
          <nav className="v-split-list" aria-label="Leads on this page">
            <header>
              <span>Leads</span>
              <span className="v-meta num">{total.toLocaleString()}</span>
            </header>
            <ul>
              {rows.map((lead) => (
                <li key={lead.id}>
                  <button
                    type="button"
                    className="v-split-item"
                    aria-current={lead.id === selected.id ? "true" : undefined}
                    onClick={() => onInspect(lead.id)}
                  >
                    <span className="v-split-item-text">
                      <b>
                        {newLeadIds.has(lead.id) && <i className="v-dot" aria-label="New lead" role="img" />}
                        {lead.company_name || lead.full_name || "Unnamed lead"}
                      </b>
                      <small>{lead.full_name || "Contact not supplied"}</small>
                    </span>
                    <span className="v-split-item-meta">
                      {(() => {
                        const view = viewDeal(lead, deals.deals[lead.id], recordedStages?.get(lead.id));
                        return (
                          <>
                            <span className="num">{view.value != null ? formatMoney(view.value, "USD", true) : lead.qualification?.total_score ?? "–"}</span>
                            <small><i className="v-stage-dot" data-stage={view.stage} aria-hidden="true" />{stageInfo(view.stage).label}</small>
                          </>
                        );
                      })()}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </nav>
        )}
        <section className="v-split-detail" aria-label="Lead details">
          <LeadRecord
            key={selected.id}
            lead={selected}
            onBack={onClose}
            activeSection={activeSection}
            onSectionChange={onSectionChange}
            isSectionPinned={isSectionPinned}
            onTogglePin={onTogglePin}
            canCrmSync={canCrmSync}
            canViewEmail={canViewEmail}
            canUseAgent={canUseAgent}
            canCall={canCall}
            canManageRouting={canManageRouting}
            canManageManualReview={canManageManualReview}
            assignmentOptions={assignmentOptions}
            recordedStage={recordedStages?.get(selected.id) ?? null}
            intent={intent}
            embeddedPhone={embeddedPhone}
            dialerPhoneConnectionId={dialerPhoneConnectionId}
            onNavigateToDialer={onNavigateToDialer}
            canEdit={canEdit}
            canEnrollAutomations={canEnrollAutomations}
            onEnrollAutomation={(kind) =>
              setEnrollment({ kind, leadId: selected.id, leadLabel: `${selected.full_name || "Unnamed lead"} · ${selected.company_name || "No company"}` })
            }
            onUpdated={(lead) => {
              onUpdated(lead);
              setRows((current) => current.map((item) => (item.id === lead.id ? lead : item)));
            }}
            onDeleted={(leadId) => {
              onDeleted(leadId);
              setRows((current) => current.filter((item) => item.id !== leadId));
              onClose();
              setRefreshKey((value) => value + 1);
            }}
          />
        </section>
        {enrollmentDialog}
      </div>
    );
  }

  return (
    <div className="v-page" data-width="wide" data-lead-page={activeSection}>
      <PageHeader
        title="Leads"
        subtitle={
          loading && !loadedOnce.current
            ? "Loading your leads…"
            : hasFilters
              ? `${total.toLocaleString()} matching ${total === 1 ? "lead" : "leads"}`
              : `${total.toLocaleString()} ${total === 1 ? "lead" : "leads"} in this workspace`
        }
        actions={
          <>
            {canExport && <Button icon="share" onClick={() => setShowExport(true)}>Export</Button>}
            {canEdit && <Button kind="filled" icon="plus" onClick={() => setShowForm(true)}>New Lead</Button>}
          </>
        }
      />

      <section className="v-queue" data-tour="leads" aria-label="Lead queue">
        <div className="v-queue-bar" role="search">
          <label className="v-searchfield">
            <Icon name="search" size={16} />
            <span className="v-sr">Search leads</span>
            <input
              type="search"
              placeholder="Search company, contact or service"
              value={draft.search}
              onChange={(event) => setDraft((current) => ({ ...current, search: event.target.value }))}
            />
          </label>
          <button
            type="button"
            className="v-filterbtn"
            aria-expanded={filtersOpen}
            aria-controls="lead-refinements"
            data-active={refinementCount > 0 || undefined}
            onClick={() => setFiltersOpen((current) => !current)}
          >
            <Icon name="filter" size={16} />
            <span>Filter</span>
            {refinementCount > 0 && <span className="v-count" data-tone="tint">{refinementCount}</span>}
          </button>
          <label className="v-lens">
            <span>View</span>
            <select className="v-popup" aria-label="Columns to show" value={lens} onChange={(event) => setLens(event.target.value as Lens)}>
              {LENSES.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </label>
          <span className="v-live" role="status" aria-live="polite">
            <i aria-hidden="true" />
            {loading && loadedOnce.current ? "Updating…" : "Live"}
          </span>
        </div>

        <div id="lead-refinements" className="v-refine" hidden={!filtersOpen}>
          <label className="v-field">
            <span>Qualification</span>
            <select aria-label="Qualification band" value={draft.band} onChange={(event) => setDraft((current) => ({ ...current, band: event.target.value }))}>
              <option value="all">Any qualification</option>
              {BANDS.map((band) => <option key={band} value={band}>{BAND_NAMES[band]}</option>)}
            </select>
          </label>
          <label className="v-field">
            <span>Source</span>
            <select aria-label="Lead source" value={draft.source} onChange={(event) => setDraft((current) => ({ ...current, source: event.target.value }))}>
              <option value="all">Any source</option>
              {SOURCES.map((source) => <option key={source} value={source}>{sourceLabel(source)}</option>)}
            </select>
          </label>
          <label className="v-field">
            <span>Received from</span>
            <input aria-label="Received from" type="date" value={draft.dateFrom} max={draft.dateTo || undefined} onChange={(event) => setDraft((current) => ({ ...current, dateFrom: event.target.value }))} />
          </label>
          <label className="v-field">
            <span>Received to</span>
            <input aria-label="Received to" type="date" value={draft.dateTo} min={draft.dateFrom || undefined} onChange={(event) => setDraft((current) => ({ ...current, dateTo: event.target.value }))} />
          </label>
        </div>

        {(hasFilters || draftDiffers) && (
          <div className="v-filter-summary">
            <span>
              {[
                applied.search.trim() && `“${applied.search.trim()}”`,
                applied.band !== "all" && BAND_NAMES[applied.band],
                applied.source !== "all" && sourceLabel(applied.source),
                (applied.dateFrom || applied.dateTo) && `${applied.dateFrom || "Any date"} – ${applied.dateTo || "today"}`,
              ].filter(Boolean).join(" · ") || "Applying…"}
            </span>
            <Button kind="plain" size="sm" onClick={clearFilters}>Clear filters</Button>
          </div>
        )}

        {notice && <Banner title={notice} action={<Button kind="plain" size="sm" onClick={() => setNotice(null)}>Dismiss</Button>} />}
        {loadError && <Banner tone="danger" title="Leads couldn’t be loaded" action={<Button size="sm" onClick={() => setRefreshKey((value) => value + 1)}>Try Again</Button>}>{loadError}</Banner>}
        {(controlError || assignmentError) && <Banner tone="danger" title={controlError || assignmentError} />}

        <div className="v-tablewrap" role="region" aria-label="Lead results" tabIndex={0} aria-busy={loading}>
          <table className="v-table v-lead-table" data-lens={lens}>
            <thead>
              <tr>
                <th scope="col" className="c-check">
                  <input
                    type="checkbox"
                    className="v-check"
                    aria-label="Select all visible leads"
                    checked={rows.length > 0 && visibleSelected === rows.length}
                    disabled={!rows.length}
                    ref={(element) => { if (element) element.indeterminate = visibleSelected > 0 && visibleSelected < rows.length; }}
                    onChange={selectVisible}
                  />
                </th>
                <th scope="col" className="c-lead">Lead</th>
                <th scope="col" className="c-score">Score</th>
                <th scope="col" className="c-a">{colA}</th>
                <th scope="col" className="c-b">{colB}</th>
                {lens === "overview" && <th scope="col" className="c-gen" title="Marked when the lead came from your team’s own outreach">Outbound</th>}
                <th scope="col" className="c-stage">Stage</th>
                <th scope="col" className="c-value">Value</th>
                <th scope="col" className="c-date">Received</th>
                <th scope="col" className="c-actions"><span className="v-sr">Quick actions</span></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((lead) => {
                const view = viewDeal(lead, deals.deals[lead.id], recordedStages?.get(lead.id));
                return (
                <LeadRow
                  key={lead.id}
                  lead={lead}
                  lens={lens}
                  isNew={newLeadIds.has(lead.id)}
                  checked={selectedIds.has(lead.id)}
                  canEditGenerated={canEdit}
                  canManageRouting={canManageRouting}
                  assignmentOptions={assignmentOptions}
                  assignmentDisabled={assignmentLoading || Boolean(assignmentError)}
                  savingAssignment={savingAssignment.has(lead.id)}
                  savingGenerated={savingGenerated.has(lead.id)}
                  onToggle={toggleSelected}
                  onInspect={onInspect}
                  onAssignmentChange={changeAssignment}
                  onGeneratedChange={changeGenerated}
                  stageLabel={stageInfo(view.stage).label}
                  stageId={view.stage}
                  value={view.value}
                  canCall={canCall}
                  canEmail={canViewEmail && canEdit}
                  canQuote={canEdit}
                />
                );
              })}
              {loading && !loadedOnce.current &&
                Array.from({ length: 7 }, (_, index) => (
                  <tr className="v-skel-row" key={`s-${index}`} aria-hidden="true">
                    <td />
                    <td><span className="v-lead-cell"><span className="v-skel" style={{ width: 32, height: 32, borderRadius: 9 }} /><span style={{ display: "grid", gap: 6, flex: 1 }}><i className="v-skel" style={{ width: "55%" }} /><i className="v-skel" style={{ width: "35%" }} /></span></span></td>
                    <td><i className="v-skel" style={{ width: 70 }} /></td>
                    <td><i className="v-skel" style={{ width: 80 }} /></td>
                    <td><i className="v-skel" style={{ width: 90 }} /></td>
                    {lens === "overview" && <td />}
                    <td><i className="v-skel" style={{ width: 64 }} /></td>
                    <td><i className="v-skel" style={{ width: 56 }} /></td>
                    <td><i className="v-skel" style={{ width: 48, marginLeft: "auto" }} /></td>
                    <td />
                  </tr>
                ))}
            </tbody>
          </table>
          {loading && !loadedOnce.current && <span className="v-sr" role="status">Loading leads…</span>}
          {!loading && rows.length === 0 && (
            <EmptyState
              icon={hasFilters ? "search" : "leads"}
              title={hasFilters ? "No leads match" : "No leads yet"}
              action={hasFilters ? <Button onClick={clearFilters}>Clear Filters</Button> : canEdit ? <Button kind="filled" icon="plus" onClick={() => setShowForm(true)}>New Lead</Button> : undefined}
            >
              {hasFilters ? "Widen the search or clear the filters to see every lead." : "Leads appear here the moment a connected source or your website form sends one."}
            </EmptyState>
          )}
        </div>

        {total > 0 && (
          <nav className="v-pager" aria-label="Lead list pages">
            <span className="num">{firstRecord.toLocaleString()}–{lastRecord.toLocaleString()} of {total.toLocaleString()}</span>
            {total > LEAD_PAGE_SIZE && (
              <span className="v-pager-controls">
                <IconButton icon="chevronLeft" label="Previous page" disabled={page === 0 || loading} onClick={() => setPage((value) => Math.max(0, value - 1))} />
                <span className="num">Page {page + 1} of {pageCount}</span>
                <IconButton icon="chevronRight" label="Next page" disabled={page >= pageCount - 1 || loading} onClick={() => setPage((value) => Math.min(pageCount - 1, value + 1))} />
              </span>
            )}
          </nav>
        )}
      </section>

      {selectedIds.size > 0 && (
        <div className="v-selectionbar" role="group" aria-label="Bulk actions">
          <b className="num">{selectedIds.size} selected</b>
          {canCrmSync && (
            <>
              <span className="v-selectionbar-sep" aria-hidden="true" />
              <select className="v-popup" aria-label="CRM to transfer selected leads to" value={bulkProvider} onChange={(event) => setBulkProvider(event.target.value)}>
                <option value="">{crmProviders.length ? "Choose CRM" : "No verified CRM"}</option>
                {crmProviders.map((provider) => <option key={provider} value={provider}>{providerLabel(provider)}</option>)}
              </select>
              <Button size="sm" disabled={!bulkProvider} onClick={() => void transferSelected()}>Transfer to CRM…</Button>
            </>
          )}
          {canEdit && <Button size="sm" kind="destructive" onClick={() => void deleteSelected()}>Delete…</Button>}
          <span className="v-selectionbar-sep" aria-hidden="true" />
          <Button size="sm" kind="plain" onClick={() => setSelectedIds(new Set())}>Clear selection</Button>
        </div>
      )}

      {showForm && (
        <NewLeadSheet
          onClose={() => setShowForm(false)}
          onCreated={(lead) => {
            setShowForm(false);
            onCreated(lead);
            setRefreshKey((value) => value + 1);
          }}
        />
      )}
      {showExport && (
        <ExportSheet count={total} selectedIds={[...selectedIds]} filters={applied} onClose={() => setShowExport(false)} />
      )}
      {enrollmentDialog}
    </div>
  );
}

/* ================================================================== Row */

const LeadRow = memo(function LeadRow({
  lead,
  lens,
  isNew,
  checked,
  canEditGenerated,
  canManageRouting,
  assignmentOptions,
  assignmentDisabled,
  savingAssignment,
  savingGenerated,
  onToggle,
  onInspect,
  onAssignmentChange,
  onGeneratedChange,
  stageLabel,
  stageId,
  value,
  canCall,
  canEmail,
  canQuote,
}: {
  stageLabel: string;
  stageId: string;
  value: number | null;
  canCall: boolean;
  canEmail: boolean;
  canQuote: boolean;
  lead: Lead;
  lens: Lens;
  isNew: boolean;
  checked: boolean;
  canEditGenerated: boolean;
  canManageRouting: boolean;
  assignmentOptions: LeadAssignmentOption[];
  assignmentDisabled: boolean;
  savingAssignment: boolean;
  savingGenerated: boolean;
  onToggle: (id: string) => void;
  onInspect: (id: string) => void;
  onAssignmentChange: (leadId: string, repId: string | null) => void;
  onGeneratedChange: (leadId: string, value: boolean) => void;
}) {
  const origin = crmOrigin(lead);
  const review = Boolean(lead.analysis?.requires_manual_review || lead.routing?.manual_review);
  const sourceDetail = origin ? "CRM import" : lead.source_detail || "";
  const name = lead.full_name || "Unnamed lead";
  const [a, b] = lensValues(lead, lens);
  return (
    <tr
      data-selected={checked || undefined}
      tabIndex={0}
      aria-label={`${name}, ${lead.company_name || "no company"}. Open lead.`}
      onClick={(event) => {
        if ((event.target as HTMLElement).closest("button, input, select, a, label")) return;
        onInspect(lead.id);
      }}
      onKeyDown={(event) => {
        if ((event.target as HTMLElement).closest("button, input, select")) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onInspect(lead.id);
        }
      }}
    >
      <td className="c-check">
        <input type="checkbox" className="v-check" aria-label={`Select ${name}`} checked={checked} onChange={() => onToggle(lead.id)} onClick={(event) => event.stopPropagation()} />
      </td>
      <td className="c-lead">
        <span className="v-lead-cell">
          <Monogram text={lead.company_name || name} size={32} />
          <span className="v-lead-name">
            <b>
              {isNew && <i className="v-dot" role="img" aria-label="New lead" />}
              {lead.company_name || name}
            </b>
            <small>{lead.company_name ? name : "Company not supplied"}{lead.job_title ? ` · ${lead.job_title}` : ""}</small>
          </span>
        </span>
      </td>
      <td className="c-score">
        <span className="v-score-cell" aria-label={`Score ${lead.qualification?.total_score ?? "unavailable"}; ${review ? "manual review hold" : titleize(lead.qualification?.classification || "pending")}`}>
          <ScoreRing score={lead.qualification?.total_score} band={lead.qualification?.classification} review={review} size={30} label="" />
          <BandBadge band={lead.qualification?.classification || "pending"} review={review} />
        </span>
      </td>
      {lens === "overview" ? (
        <>
          <td className="c-a">
            <span className="v-two">
              <span>{origin || sourceLabel(lead.source)}</span>
              {sourceDetail && <small title={sourceDetail}>{sourceDetail}</small>}
            </span>
          </td>
          <td className="c-b">
            {canManageRouting && lead.routing ? (
              <span className="v-two">
                <select
                  className="v-popup"
                  aria-label={`Assigned representative for ${lead.full_name}`}
                  value={lead.routing.assigned_rep_id || ""}
                  disabled={savingAssignment || assignmentDisabled}
                  onClick={(event) => event.stopPropagation()}
                  onChange={(event) => onAssignmentChange(lead.id, event.target.value || null)}
                >
                  <option value="">Unassigned</option>
                  {lead.routing.assigned_rep_id && !assignmentOptions.some((option) => option.user_id === lead.routing?.assigned_rep_id) && (
                    <option value={lead.routing.assigned_rep_id}>{lead.routing.assigned_rep_name || "Current representative"}</option>
                  )}
                  {assignmentOptions.map((option) => <option key={option.user_id} value={option.user_id}>{option.display_name}</option>)}
                </select>
                <small>{savingAssignment ? "Saving…" : queueLabel(lead.routing.assigned_queue)}</small>
              </span>
            ) : (
              <span className="v-two">
                <span>{lead.routing ? lead.routing.assigned_rep_name || "Unassigned" : "Routing pending"}</span>
                {lead.routing && <small>{queueLabel(lead.routing.assigned_queue)}</small>}
              </span>
            )}
          </td>
          <td className="c-gen">
            <input
              type="checkbox"
              role="switch"
              className="v-switch"
              aria-label={`Lead generated by your team: ${lead.full_name}`}
              checked={lead.is_generated}
              disabled={!canEditGenerated || savingGenerated}
              onClick={(event) => event.stopPropagation()}
              onChange={(event) => onGeneratedChange(lead.id, event.target.checked)}
            />
          </td>
        </>
      ) : (
        <>
          <td className="c-a"><span className="v-one" title={a}>{a}</span></td>
          <td className="c-b"><span className="v-one" title={b}>{b}</span></td>
        </>
      )}
      <td className="c-stage"><span className="v-stage-chip" data-stage={stageId}><i className="v-stage-dot" data-stage={stageId} aria-hidden="true" />{stageLabel}</span></td>
      <td className="c-value num">{value != null ? formatMoney(value) : <span className="v-muted">—</span>}</td>
      <td className="c-date">
        <time dateTime={lead.received_at} title={new Date(lead.received_at).toLocaleString()}>{formatShortDate(lead.received_at)}</time>
      </td>
      <td className="c-actions">
        <span className="v-row-actions">
          {canCall && lead.phone && <button type="button" className="b-act" aria-label={`Call ${name}`} title="Call" onClick={() => leadAction(lead.id, "call")}><Icon name="phone" size={14} /></button>}
          {canEmail && lead.email && <button type="button" className="b-act" aria-label={`Email ${name}`} title="Email" onClick={() => leadAction(lead.id, "email")}><Icon name="mail" size={14} /></button>}
          {canQuote && <button type="button" className="b-act" aria-label={`Build a quote for ${name}`} title="Quote" onClick={() => leadAction(lead.id, "quote")}><Icon name="quote" size={14} /></button>}
          <button type="button" className="b-act" aria-label={`Open ${name}`} title="Open" onClick={() => onInspect(lead.id)}><Icon name="chevronRight" size={14} /></button>
        </span>
      </td>
    </tr>
  );
});

/* ============================================================ New lead */

function NewLeadSheet({ onClose, onCreated }: { onClose: () => void; onCreated: (lead: Lead) => void }) {
  const [form, setForm] = useState<LeadPayload>(emptyLead);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (key: keyof LeadPayload, value: string | number | null) => setForm((current) => ({ ...current, [key]: value }));
  async function submit(event?: FormEvent) {
    event?.preventDefault();
    const hasDetail = Object.entries(form).some(([key, value]) => !["source", "source_detail"].includes(key) && value !== null && String(value).trim().length > 0);
    if (!hasDetail) {
      setError("Enter at least one detail you know; the other fields can stay blank.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      onCreated(await createLead(form));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to submit lead");
    } finally {
      setBusy(false);
    }
  }
  const text = (label: string, key: keyof LeadPayload, type = "text", autoComplete = "off") => (
    <label className="v-field">
      <span>{label}</span>
      <input
        type={type}
        autoComplete={autoComplete}
        value={(form[key] as string | number | null) ?? ""}
        onChange={(event) => set(key, type === "number" ? (event.target.value ? Number(event.target.value) : null) : event.target.value)}
      />
    </label>
  );
  return (
    <Sheet
      title="New Lead"
      subtitle="Every field is optional. Corral analyses what you enter, then scores it with the same rules as any other lead."
      onClose={() => { if (!busy) onClose(); }}
      dismissible={!busy}
      width={640}
      footer={
        <>
          <Button onClick={onClose} disabled={busy}>Cancel</Button>
          <Button kind="filled" onClick={() => void submit()} disabled={busy}>{busy ? "Analyzing…" : "Analyze and Create"}</Button>
        </>
      }
    >
      <form className="v-form" onSubmit={(event) => void submit(event)}>
        <fieldset className="v-form-group">
          <legend>Contact</legend>
          <div className="v-form-grid">
            {text("Full name", "full_name", "text", "name")}
            {text("Email", "email", "email", "email")}
            {text("Phone", "phone", "tel", "tel")}
            {text("Job title", "job_title", "text", "organization-title")}
          </div>
        </fieldset>
        <fieldset className="v-form-group">
          <legend>Company</legend>
          <div className="v-form-grid">
            {text("Company", "company_name", "text", "organization")}
            {text("Industry", "industry")}
            {text("Geography", "geography")}
            {text("Employees", "employee_count", "number")}
            {text("Budget (USD)", "budget", "number")}
            {text("Service requested", "product_or_service")}
          </div>
        </fieldset>
        <label className="v-field">
          <span>Inquiry or process context</span>
          <textarea
            rows={5}
            value={form.message}
            onChange={(event) => set("message", event.target.value)}
            placeholder="Paste the original inquiry, or summarise the need, steps taken, timing and open questions."
          />
          <small>Corral treats this as context, not necessarily a verbatim quote.</small>
        </label>
        {error && <Banner tone="danger" title={error} />}
        <button type="submit" hidden />
      </form>
    </Sheet>
  );
}

/* ============================================================== Export */

function ExportSheet({ count, selectedIds, filters, onClose }: { count: number; selectedIds: string[]; filters: Filters; onClose: () => void }) {
  const [format, setFormat] = useState<"csv" | "xlsx" | "pdf">("xlsx");
  const [scope, setScope] = useState<"current_filters" | "selected" | "all">("current_filters");
  const [columns, setColumns] = useState<LeadExportColumn[]>(() => LEAD_EXPORT_COLUMNS.filter((column) => column.defaultSelected).map((column) => column.key));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toggleColumn = (column: LeadExportColumn, checked: boolean) =>
    setColumns((current) => {
      const chosen = new Set(current);
      if (checked) chosen.add(column);
      else chosen.delete(column);
      return LEAD_EXPORT_COLUMNS.filter((option) => chosen.has(option.key)).map((option) => option.key);
    });
  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const result = await exportLeads({
        format,
        scope,
        columns,
        filters: scope === "current_filters"
          ? { search: filters.search, source: filters.source === "all" ? null : filters.source, qualification_bands: filters.band === "all" ? [] : [filters.band], date_from: filters.dateFrom || null, date_to: filters.dateTo || null }
          : {},
        selected_lead_ids: scope === "selected" ? selectedIds : [],
      });
      const url = URL.createObjectURL(result.blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = result.filename;
      anchor.click();
      URL.revokeObjectURL(url);
      onClose();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to generate export");
    } finally {
      setBusy(false);
    }
  }
  const scopes: Array<{ value: typeof scope; label: string; detail: string; disabled?: boolean }> = [
    { value: "current_filters", label: "Current results", detail: `${count.toLocaleString()} leads with the filters you’ve applied` },
    { value: "selected", label: "Selected leads", detail: `${selectedIds.length} selected`, disabled: !selectedIds.length },
    { value: "all", label: "Every lead you can see", detail: "All permitted leads in this workspace" },
  ];
  return (
    <Sheet
      title="Export Leads"
      subtitle="The file is generated on the server from the leads you’re allowed to see."
      onClose={() => { if (!busy) onClose(); }}
      dismissible={!busy}
      width={620}
      footer={
        <>
          <Button onClick={onClose} disabled={busy}>Cancel</Button>
          <Button kind="filled" icon="download" onClick={() => void submit()} disabled={busy || columns.length === 0 || (scope === "selected" && !selectedIds.length)}>
            {busy ? "Preparing…" : "Download"}
          </Button>
        </>
      }
    >
      <div className="v-form">
        <div className="v-field">
          <span id="export-format-label">Format</span>
          <Segmented
            label="Export format"
            value={format}
            onChange={setFormat}
            options={[{ value: "xlsx", label: "Excel" }, { value: "csv", label: "CSV" }, { value: "pdf", label: "PDF" }]}
          />
        </div>
        <fieldset className="v-form-group">
          <legend>Leads to include</legend>
          <div className="v-choices">
            {scopes.map((option) => (
              <label key={option.value} className="v-choice" data-disabled={option.disabled || undefined}>
                <input type="radio" name="export-scope" checked={scope === option.value} disabled={option.disabled} onChange={() => setScope(option.value)} />
                <span><b>{option.label}</b><small>{option.detail}</small></span>
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset className="v-form-group">
          <legend>
            Columns <span className="v-meta num">{columns.length} of {LEAD_EXPORT_COLUMNS.length}</span>
          </legend>
          <div className="v-inline-actions">
            <Button kind="plain" size="sm" disabled={busy || columns.length === LEAD_EXPORT_COLUMNS.length} onClick={() => setColumns(LEAD_EXPORT_COLUMNS.map((column) => column.key))}>Select All</Button>
            <Button kind="plain" size="sm" disabled={busy || columns.length === 0} onClick={() => setColumns([])}>Clear</Button>
          </div>
          <div className="v-columns">
            {LEAD_EXPORT_COLUMNS.map((column) => (
              <label key={column.key} className="v-colcheck">
                <input type="checkbox" className="v-check" checked={columns.includes(column.key)} disabled={busy} onChange={(event) => toggleColumn(column.key, event.currentTarget.checked)} />
                <span>{column.label}</span>
              </label>
            ))}
          </div>
          {columns.length === 0 && <p className="v-hint is-error" role="alert">Choose at least one column.</p>}
          {format === "pdf" && columns.length > 10 && <p className="v-hint">A PDF reads best with ten columns or fewer.</p>}
        </fieldset>
        {error && <Banner tone="danger" title={error} />}
      </div>
    </Sheet>
  );
}
