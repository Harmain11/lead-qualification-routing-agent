import { OperationsDialog } from "../components/OperationsDialog";
import type { FormEvent } from "react";
import { memo, useEffect, useRef, useState } from "react";
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
} from "../api";
import { AutomationEnrollmentDialog } from "../components/AutomationEnrollmentDialog";
import { LeadDetail } from "../components/LeadDetail";
import { LeadRowIdentity } from "../components/LeadRowIdentity";
import { LeadSubpage, leadSubpageCopy } from "../components/LeadSubpage";
import { SelectMenu } from "../components/SelectMenu";
import type { LeadDetailSection } from "../leadSections";
import type { Lead, LeadAssignmentOption, LeadPayload } from "../types";
import {
  leadSourceCategory,
  leadSourceDetailLabel,
  leadSourceLabel,
} from "../leadSources";
import {
  Empty,
  pretty,
  QualificationBadge,
  qualificationLabel,
} from "./productShared";

const emptyLead: LeadPayload = {
  source: "manual",
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

const LEAD_PAGE_SIZE = 50;

type LeadFilterState = {
  search: string;
  band: string;
  source: string;
  dateFrom: string;
  dateTo: string;
};

const emptyLeadFilters = (): LeadFilterState => ({
  search: "",
  band: "all",
  source: "all",
  dateFrom: "",
  dateTo: "",
});

const LEAD_SOURCE_OPTIONS = [
  "manual",
  "email",
  "website",
  "offline",
  "unknown",
  "purchased_list",
  "web",
  "phone_inquiry",
  "partner_referral",
  "clutch",
  "salesforce",
  "hubspot",
  "gohighlevel",
];

const crmOrigin = (lead: Lead) => {
  const match = lead.source_detail?.match(/^crm-import:(salesforce|hubspot|gohighlevel):/i);
  return match ? pretty(match[1]) : null;
};

export function LeadsView({
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
  canExport = false,
  canCrmSync = false,
  canViewEmail = false,
  canUseAgent = false,
  dialerPhoneConnectionId = null,
  onNavigateToDialer,
  canEnrollAutomations = false,
  onOpenAutomations,
}: {
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
  canExport?: boolean;
  canCrmSync?: boolean;
  canViewEmail?: boolean;
  canUseAgent?: boolean;
  dialerPhoneConnectionId?: string | null;
  onNavigateToDialer: () => void;
  canEnrollAutomations?: boolean;
  onOpenAutomations?: () => void;
}) {
  const [search, setSearch] = useState("");
  const [band, setBand] = useState("all");
  const [source, setSource] = useState("all");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [appliedFilters, setAppliedFilters] = useState<LeadFilterState>(
    emptyLeadFilters,
  );
  const [rows, setRows] = useState<Lead[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const hasLoadedRows = useRef(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [showExport, setShowExport] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [crmNotice, setCrmNotice] = useState<string | null>(null);
  const [leadControlError, setLeadControlError] = useState<string | null>(null);
  const [crmProviders, setCrmProviders] = useState<string[]>([]);
  const [bulkProvider, setBulkProvider] = useState("");
  const [assignmentOptions, setAssignmentOptions] = useState<LeadAssignmentOption[]>([]);
  const [assignmentOptionsLoading, setAssignmentOptionsLoading] = useState(false);
  const [assignmentOptionsError, setAssignmentOptionsError] = useState<string | null>(null);
  const [savingAssignmentIds, setSavingAssignmentIds] = useState<Set<string>>(new Set());
  const [savingGeneratedIds, setSavingGeneratedIds] = useState<Set<string>>(new Set());
  const [automationEnrollment, setAutomationEnrollment] = useState<{
    kind: "sequence" | "playbook";
    leadId: string;
    leadLabel: string;
  } | null>(null);
  useEffect(() => {
    if (!canManageRouting) {
      setAssignmentOptions([]);
      setAssignmentOptionsLoading(false);
      setAssignmentOptionsError(null);
      return;
    }
    let active = true;
    const controller = new AbortController();
    setAssignmentOptionsLoading(true);
    setAssignmentOptionsError(null);
    void listLeadAssignmentOptions(controller.signal)
      .then((options) => {
        if (active) setAssignmentOptions(options);
      })
      .catch((error) => {
        if (active && !controller.signal.aborted) {
          setAssignmentOptionsError(
            error instanceof Error
              ? error.message
              : "Unable to load workspace representatives.",
          );
        }
      })
      .finally(() => {
        if (active) setAssignmentOptionsLoading(false);
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [canManageRouting]);
  useEffect(() => {
    if (!canCrmSync) return;
    let active = true;
    void listIntegrations()
      .then((cards) => {
        if (!active) return;
        const providers = cards
          .filter(
            (card) =>
              card.category.toLowerCase() === "crm" &&
              card.directionality.manual_outbound &&
              card.connection?.is_active &&
              card.connection.verification_status === "verified",
          )
          .map((card) => card.provider);
        setCrmProviders(providers);
        setBulkProvider((current) =>
          providers.includes(current)
            ? current
            : providers.length === 1
              ? providers[0]
              : "",
        );
      })
      .catch(() => {
        if (active) setCrmProviders([]);
      });
    return () => {
      active = false;
    };
  }, [canCrmSync]);
  useEffect(() => {
    setPage(0);
    setSelectedIds(new Set());
  }, [
    appliedFilters.search,
    appliedFilters.band,
    appliedFilters.source,
    appliedFilters.dateFrom,
    appliedFilters.dateTo,
  ]);
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible")
        setRefreshKey((value) => value + 1);
    }, 5000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      if (!hasLoadedRows.current) setLoading(true);
      setLoadError(null);
      void listLeads(
        {
          limit: LEAD_PAGE_SIZE,
          offset: page * LEAD_PAGE_SIZE,
          search: appliedFilters.search.trim() || undefined,
          source:
            appliedFilters.source === "all" ? undefined : appliedFilters.source,
          qualification_band:
            appliedFilters.band === "all" ? undefined : appliedFilters.band,
          date_from: appliedFilters.dateFrom || undefined,
          date_to: appliedFilters.dateTo || undefined,
        },
        controller.signal,
      )
        .then((result) => {
          if (!active) return;
          setRows(result.items);
          setTotal(result.total);
          hasLoadedRows.current = true;
        })
        .catch((reason) => {
          if (active && !controller.signal.aborted) {
            setLoadError(
              reason instanceof Error ? reason.message : "Unable to load leads",
            );
          }
        })
        .finally(() => {
          if (active) setLoading(false);
        });
    }, 180);
    return () => {
      active = false;
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [appliedFilters, page, refreshKey, revision]);

  const visibleSelected = rows.filter((lead) =>
    selectedIds.has(lead.id),
  ).length;
  const toggleSelected = (id: string) =>
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const updateLeadRow = (lead: Lead) => {
    setRows((current) =>
      current.map((item) => (item.id === lead.id ? lead : item)),
    );
    onUpdated(lead);
  };
  const changeLeadGenerated = async (leadId: string, isGenerated: boolean) => {
    setLeadControlError(null);
    setSavingGeneratedIds((current) => new Set(current).add(leadId));
    try {
      updateLeadRow(await updateLeadGenerated(leadId, isGenerated));
    } catch (error) {
      setLeadControlError(
        error instanceof Error ? error.message : "Unable to update lead status.",
      );
    } finally {
      setSavingGeneratedIds((current) => {
        const next = new Set(current);
        next.delete(leadId);
        return next;
      });
    }
  };
  const changeLeadAssignment = async (
    leadId: string,
    assignedRepId: string | null,
  ) => {
    setLeadControlError(null);
    setSavingAssignmentIds((current) => new Set(current).add(leadId));
    try {
      updateLeadRow(await updateLeadAssignment(leadId, assignedRepId));
    } catch (error) {
      setLeadControlError(
        error instanceof Error
          ? error.message
          : "Unable to update the assigned representative.",
      );
    } finally {
      setSavingAssignmentIds((current) => {
        const next = new Set(current);
        next.delete(leadId);
        return next;
      });
    }
  };
  const selectVisible = () =>
    setSelectedIds((current) => {
      const next = new Set(current);
      if (visibleSelected === rows.length)
        rows.forEach((lead) => next.delete(lead.id));
      else rows.forEach((lead) => next.add(lead.id));
      return next;
    });
  const pageCount = Math.max(1, Math.ceil(total / LEAD_PAGE_SIZE));
  const firstRecord = total === 0 ? 0 : page * LEAD_PAGE_SIZE + 1;
  const lastRecord = Math.min((page + 1) * LEAD_PAGE_SIZE, total);
  const hasFilters = Boolean(
    appliedFilters.search.trim() ||
      appliedFilters.band !== "all" ||
      appliedFilters.source !== "all" ||
      appliedFilters.dateFrom ||
      appliedFilters.dateTo,
  );
  const hasDraftChanges =
    search !== appliedFilters.search ||
    band !== appliedFilters.band ||
    source !== appliedFilters.source ||
    dateFrom !== appliedFilters.dateFrom ||
    dateTo !== appliedFilters.dateTo;
  const applyFilters = () =>
    setAppliedFilters({ search, band, source, dateFrom, dateTo });
  const clearFilters = () => {
    const empty = emptyLeadFilters();
    setSearch(empty.search);
    setBand(empty.band);
    setSource(empty.source);
    setDateFrom(empty.dateFrom);
    setDateTo(empty.dateTo);
    setAppliedFilters(empty);
  };
  const deleteSelected = async () => {
    if (
      !selectedIds.size ||
      !window.confirm(
        `Delete ${selectedIds.size} selected lead${selectedIds.size === 1 ? "" : "s"}? A later matching inquiry can be imported again.`,
      )
    )
      return;
    try {
      const result = await bulkDeleteLeads([...selectedIds]);
      setSelectedIds(new Set());
      setCrmNotice(
        `${result.deleted} lead${result.deleted === 1 ? "" : "s"} deleted. Later matching inquiries can be imported again.`,
      );
      setRefreshKey((value) => value + 1);
      void onDeleted("bulk");
    } catch (error) {
      setCrmNotice(
        error instanceof Error
          ? error.message
          : "Unable to delete selected leads",
      );
    }
  };

  return (
    <div
      className="page-view operational-page focused-page"
      data-lead-page={activeSection}
    >
      {selected ? (
        <section className="lead-full-page lead-drawer" aria-label="Lead details">
          <div className="lead-drawer-toolbar">
            <span>Lead details · {selected.source}</span>
            <button
              className="lead-drawer-close"
              type="button"
              autoFocus
              aria-label="Back to leads"
              onClick={onClose}
            >
              <span aria-hidden="true">←</span> Back to leads
            </button>
          </div>
          <LeadDetail
            key={selected.id}
            lead={selected}
            activeSection={activeSection}
            onSectionChange={onSectionChange}
            isSectionPinned={isSectionPinned}
            onTogglePin={onTogglePin}
            canCrmSync={canCrmSync}
            canViewEmail={canViewEmail}
            canUseAgent={canUseAgent}
            dialerPhoneConnectionId={dialerPhoneConnectionId}
            onNavigateToDialer={onNavigateToDialer}
            canEdit={canEdit}
            canEnrollAutomations={canEnrollAutomations}
            onEnrollAutomation={(kind) =>
              setAutomationEnrollment({
                kind,
                leadId: selected.id,
                leadLabel: `${selected.full_name || "Unnamed lead"} · ${selected.company_name || "No company"}`,
              })
            }
            onUpdated={(lead) => {
              onUpdated(lead);
              setRows((current) =>
                current.map((item) => (item.id === lead.id ? lead : item)),
              );
            }}
            onDeleted={(leadId) => {
              onDeleted(leadId);
              setRows((current) =>
                current.filter((item) => item.id !== leadId),
              );
              onClose();
              setRefreshKey((value) => value + 1);
            }}
          />
        </section>
      ) : (
        <>
      <header className="page-hero compact-hero">
        <div>
          <span className="eyebrow">
            Leads / {leadSubpageCopy[activeSection].title}
          </span>
          <h1>{leadSubpageCopy[activeSection].title}</h1>
          <p>{leadSubpageCopy[activeSection].description}</p>
        </div>
        <div className="lead-hero-actions" role="group" aria-label="Lead actions">
          {canExport && (
            <button
              className="secondary-button"
              onClick={() => setShowExport(true)}
            >
              Export
            </button>
          )}
          {canEdit && (
            <button
              className="primary-button"
              onClick={() => setShowForm((current) => !current)}
            >
              {showForm ? "Close intake" : "+ Submit lead"}
            </button>
          )}
        </div>
      </header>
      {showForm && (
        <LeadForm
          onCreated={(lead) => {
            onCreated(lead);
            setRefreshKey((value) => value + 1);
          }}
        />
      )}
      <section className="lead-browser panel" data-tour="leads">
        <div className="lead-toolbar">
          <label>
            <span>Search leads</span>
            <input
              type="search"
              placeholder="Search company, contact, service…"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
          <label><span>Qualification band</span>
          <SelectMenu
            ariaLabel="Qualification band"
            value={band}
            onChange={setBand}
            options={[{ value: "all", label: "All qualification bands" }, ...["hot", "warm", "needs_qualifying", "nurture", "disqualify"].map((item) => ({ value: item, label: qualificationLabel(item) }))]}
          />
          </label>
          <label><span>Lead source</span>
          <SelectMenu
            ariaLabel="Lead source"
            value={source}
            onChange={setSource}
            options={[{ value: "all", label: "All channels" }, ...LEAD_SOURCE_OPTIONS.map((item) => ({ value: item, label: leadSourceLabel(item) }))]}
          />
          </label>
          <label className="date-filter">
            <span>Received from</span>
            <input
              aria-label="Received from"
              type="date"
              value={dateFrom}
              onChange={(event) => setDateFrom(event.target.value)}
            />
          </label>
          <label className="date-filter">
            <span>Received to</span>
            <input
              aria-label="Received to"
              type="date"
              value={dateTo}
              onChange={(event) => setDateTo(event.target.value)}
            />
          </label>
          <div className="lead-filter-summary">
            <span className="live-feed-status">
              <i />
              {total.toLocaleString()} records · live
            </span>
            <div className="lead-filter-actions">
              <button
                type="button"
                className="primary-button"
                disabled={!hasDraftChanges}
                onClick={applyFilters}
              >
                Apply filters
              </button>
              <button
                type="button"
                className="button-quiet"
                disabled={!hasDraftChanges && !hasFilters}
                onClick={clearFilters}
              >
                Clear filters
              </button>
            </div>
          </div>
        </div>
        {rows.length > 0 && (
          <div className="lead-selection-bar">
            <label>
              <input
                type="checkbox"
                checked={rows.length > 0 && visibleSelected === rows.length}
                onChange={selectVisible}
              />{" "}
              Select visible
            </label>
            <span>{selectedIds.size} selected</span>
            {canCrmSync && (
              <>
                <label className="lead-transfer-control">
                  <span>Transfer to</span>
                  <SelectMenu
                    ariaLabel="Bulk CRM provider"
                    value={bulkProvider}
                    onChange={setBulkProvider}
                    options={[{ value: "", label: "Select CRM" }, ...crmProviders.map((provider) => ({ value: provider, label: provider === "hubspot" ? "HubSpot" : pretty(provider) }))]}
                  />
                </label>
                <button
                  className="button-quiet"
                  disabled={!selectedIds.size || !bulkProvider}
                  onClick={async () => {
                    if (
                      !window.confirm(
                        `Transfer or update ${selectedIds.size} selected leads in ${bulkProvider === "hubspot" ? "HubSpot" : pretty(bulkProvider)}?`,
                      )
                    )
                      return;
                    try {
                      const result = await bulkSendLeadsToCrm(
                        [...selectedIds],
                        bulkProvider,
                      );
                      const providerLabel =
                        bulkProvider === "hubspot"
                          ? "HubSpot"
                          : pretty(bulkProvider);
                      const firstFailure = result.results.find(
                        (item) => item.status !== "synced" && item.error,
                      )?.error;
                      setCrmNotice(
                        `${result.succeeded} ${providerLabel} transfers completed; ${result.failed} skipped or failed.${firstFailure ? ` ${firstFailure}` : ""}`,
                      );
                      setRefreshKey((value) => value + 1);
                    } catch (error) {
                      setCrmNotice(
                        error instanceof Error
                          ? error.message
                          : "Bulk CRM transfer failed",
                      );
                    }
                  }}
                >
                  Transfer selected
                </button>
              </>
            )}
            {canEdit && (
              <button
                className="danger-button"
                disabled={!selectedIds.size}
                onClick={() => void deleteSelected()}
              >
                Delete selected
              </button>
            )}
            <button
              className="button-quiet"
              disabled={!selectedIds.size}
              onClick={() => setSelectedIds(new Set())}
            >
              Clear selection
            </button>
          </div>
        )}
        {crmNotice && (
          <div className="notice" role="status">
            {crmNotice}
          </div>
        )}
        {loadError && (
          <div className="notice" role="alert">
            <span>{loadError}</span>
            <button
              className="button-quiet"
              onClick={() => setRefreshKey((value) => value + 1)}
            >
              Retry
            </button>
          </div>
        )}
        {activeSection !== "overview" && (
          <LeadSubpage
            section={activeSection}
            rows={rows}
            selectedIds={selectedIds}
            onToggleSelected={toggleSelected}
            onInspect={onInspect}
            loading={loading}
          />
        )}
        {(leadControlError || assignmentOptionsError) && (
          <p className="form-error" role="alert">
            {leadControlError || assignmentOptionsError}
          </p>
        )}
        <div
          className="lead-table-wrap"
          hidden={activeSection !== "overview"}
          role="region"
          aria-label="Lead results table; scroll horizontally to view every column"
          tabIndex={0}
          aria-busy={loading}
        >
          {rows.length > 0 && <table className="lead-table">
            <thead>
              <tr>
                <th aria-label="Select leads">Select</th>
                <th>Lead</th>
                <th>Source</th>
                <th>Score</th>
                <th>Routing</th>
                <th>Assigned rep</th>
                <th>Lead generated</th>
                <th>Received</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((lead) => (
                <LeadTableRow
                  key={lead.id}
                  lead={lead}
                  isNew={newLeadIds.has(lead.id)}
                  selected={false}
                  checked={selectedIds.has(lead.id)}
                  canEditGenerated={canEdit}
                  canManageRouting={canManageRouting}
                  assignmentOptions={assignmentOptions}
                  assignmentOptionsLoading={assignmentOptionsLoading}
                  assignmentOptionsError={Boolean(assignmentOptionsError)}
                  savingAssignment={savingAssignmentIds.has(lead.id)}
                  savingGenerated={savingGeneratedIds.has(lead.id)}
                  onToggle={toggleSelected}
                  onInspect={onInspect}
                  onAssignmentChange={changeLeadAssignment}
                  onGeneratedChange={changeLeadGenerated}
                />
              ))}
            </tbody>
          </table>}
          {loading && (
            <div className="table-state" role="status">
              Loading workspace leads…
            </div>
          )}
          {!loading && rows.length === 0 && (
            <Empty
              title={hasFilters ? "No matching leads" : "No leads yet"}
              text={
                hasFilters
                  ? "Adjust the filters to widen your search."
                  : "Connect a source or add your first lead to get started."
              }
            />
          )}
        </div>
        {total > LEAD_PAGE_SIZE && (
          <nav className="lead-pagination" aria-label="Lead list pages">
            <span>
              Showing {firstRecord.toLocaleString()}–
              {lastRecord.toLocaleString()} of {total.toLocaleString()}
            </span>
            <div>
              <button
                className="button-quiet"
                disabled={page === 0 || loading}
                onClick={() => setPage((value) => Math.max(0, value - 1))}
              >
                Previous
              </button>
              <span>
                Page {page + 1} of {pageCount}
              </span>
              <button
                className="button-quiet"
                disabled={page >= pageCount - 1 || loading}
                onClick={() =>
                  setPage((value) => Math.min(pageCount - 1, value + 1))
                }
              >
                Next
              </button>
            </div>
          </nav>
        )}
      </section>
      {showExport && (
        <ExportDialog
          count={total}
          selectedCount={selectedIds.size}
          filters={appliedFilters}
          onClose={() => setShowExport(false)}
          selectedIds={[...selectedIds]}
        />
      )}
        </>
      )}
      {automationEnrollment && (
        <AutomationEnrollmentDialog
          key={`${automationEnrollment.kind}-${automationEnrollment.leadId}`}
          kind={automationEnrollment.kind}
          fixedLeadIds={[automationEnrollment.leadId]}
          fixedLeadLabel={automationEnrollment.leadLabel}
          onClose={() => setAutomationEnrollment(null)}
          onOpenAutomations={onOpenAutomations}
        />
      )}
    </div>
  );
}

function ExportDialog({
  count,
  selectedCount,
  selectedIds,
  filters,
  onClose,
}: {
  count: number;
  selectedCount: number;
  selectedIds: string[];
  filters: {
    search: string;
    band: string;
    source: string;
    dateFrom: string;
    dateTo: string;
  };
  onClose: () => void;
}) {
  const [format, setFormat] = useState<"csv" | "xlsx" | "pdf">("xlsx");
  const [scope, setScope] = useState<"current_filters" | "selected" | "all">(
    "current_filters",
  );
  const [columns, setColumns] = useState<LeadExportColumn[]>(() =>
    LEAD_EXPORT_COLUMNS
      .filter((column) => column.defaultSelected)
      .map((column) => column.key),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  function toggleColumn(column: LeadExportColumn, checked: boolean) {
    setColumns((current) => {
      const selected = new Set(current);
      if (checked) selected.add(column);
      else selected.delete(column);
      return LEAD_EXPORT_COLUMNS
        .filter((option) => selected.has(option.key))
        .map((option) => option.key);
    });
  }
  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const result = await exportLeads({
        format,
        scope,
        columns,
        filters:
          scope === "current_filters"
            ? {
                search: filters.search,
                source: filters.source === "all" ? null : filters.source,
                qualification_bands:
                  filters.band === "all" ? [] : [filters.band],
                date_from: filters.dateFrom || null,
                date_to: filters.dateTo || null,
              }
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
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Unable to generate export",
      );
    } finally {
      setBusy(false);
    }
  }
  const summary =
    scope === "all"
      ? "All permitted workspace leads"
      : `${scope === "selected" ? selectedCount : count} leads${filters.band !== "all" ? ` · ${qualificationLabel(filters.band)}` : ""}${filters.source !== "all" ? ` · ${leadSourceLabel(filters.source)}` : ""}${filters.dateFrom || filters.dateTo ? ` · ${filters.dateFrom || "Any"} – ${filters.dateTo || "Any"}` : ""}`;
  return (
    <OperationsDialog title="Take leads with you" className="team-modal export-dialog" showHeader={false} onClose={() => { if (!busy) onClose(); }}>
        <div className="team-modal-head">
          <div>
            <span className="eyebrow">Secure workspace export</span>
            <h2 id="export-title">Take leads with you</h2>
            <p>{summary}</p>
          </div>
          <button
            className="modal-close"
            onClick={onClose}
            aria-label="Close export dialog"
          >
            ×
          </button>
        </div>
        <fieldset>
          <legend>Format</legend>
          <label>
            <input
              type="radio"
              name="export-format"
              checked={format === "csv"}
              onChange={() => setFormat("csv")}
            />{" "}
            CSV
          </label>
          <label>
            <input
              type="radio"
              name="export-format"
              checked={format === "xlsx"}
              onChange={() => setFormat("xlsx")}
            />{" "}
            Excel workbook
          </label>
          <label>
            <input
              type="radio"
              name="export-format"
              checked={format === "pdf"}
              onChange={() => setFormat("pdf")}
            />{" "}
            PDF report
          </label>
        </fieldset>
        <fieldset>
          <legend>Scope</legend>
          <label>
            <input
              type="radio"
              name="export-scope"
              checked={scope === "current_filters"}
              onChange={() => setScope("current_filters")}
            />{" "}
            Current filtered results ({count})
          </label>
          <label>
            <input
              type="radio"
              name="export-scope"
              disabled={!selectedCount}
              checked={scope === "selected"}
              onChange={() => setScope("selected")}
            />{" "}
            Selected leads ({selectedCount})
          </label>
          <label>
            <input
              type="radio"
              name="export-scope"
              checked={scope === "all"}
              onChange={() => setScope("all")}
            />{" "}
            All permitted workspace leads
          </label>
        </fieldset>
        <fieldset className="export-columns">
          <legend>Columns</legend>
          <p className="export-columns-note">
            Choose the fields to include in your CSV, Excel workbook, or PDF. The selected columns keep this order.
          </p>
          <div className="export-column-controls">
            <span>{columns.length} of {LEAD_EXPORT_COLUMNS.length} selected</span>
            <div>
              <button
                className="button-quiet"
                type="button"
                disabled={busy || columns.length === LEAD_EXPORT_COLUMNS.length}
                onClick={() => setColumns(LEAD_EXPORT_COLUMNS.map((column) => column.key))}
              >
                Select all
              </button>
              <button
                className="button-quiet"
                type="button"
                disabled={busy || columns.length === 0}
                onClick={() => setColumns([])}
              >
                Clear
              </button>
            </div>
          </div>
          <div className="export-column-grid">
            {LEAD_EXPORT_COLUMNS.map((column) => (
              <label key={column.key}>
                <input
                  type="checkbox"
                  checked={columns.includes(column.key)}
                  disabled={busy}
                  onChange={(event) => toggleColumn(column.key, event.currentTarget.checked)}
                />
                {column.label}
              </label>
            ))}
          </div>
          {columns.length === 0 && (
            <small className="export-columns-warning" role="alert">
              Select at least one column before downloading.
            </small>
          )}
          {format === "pdf" && columns.length > 10 && (
            <small className="export-columns-note">
              For a more readable PDF, consider selecting fewer columns.
            </small>
          )}
        </fieldset>
        {error && (
          <p className="team-alert" role="alert">
            {error}
          </p>
        )}
        <div className="team-modal-actions">
          <button className="button-quiet" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button
            className="primary-button"
            onClick={() => void submit()}
            disabled={busy || columns.length === 0 || (scope === "selected" && !selectedCount)}
          >
            {busy ? "Preparing…" : "Download export"}
          </button>
        </div>
    </OperationsDialog>
  );
}

function LeadForm({ onCreated }: { onCreated: (lead: Lead) => void }) {
  const [form, setForm] = useState<LeadPayload>(emptyLead);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (key: keyof LeadPayload, value: string | number | null) =>
    setForm((current) => ({ ...current, [key]: value }));
  async function submit(event: FormEvent) {
    event.preventDefault();
    const hasKnownDetail = Object.entries(form).some(
      ([key, value]) =>
        !["source", "source_detail"].includes(key) &&
        value !== null &&
        String(value).trim().length > 0,
    );
    if (!hasKnownDetail) {
      setError("Enter at least one known lead detail; the other fields can stay blank.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const lead = await createLead(form);
      onCreated(lead);
      setForm(emptyLead);
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Unable to submit lead",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="intake-form panel" onSubmit={submit}>
      <div className="section-heading">
        <div>
          <span className="eyebrow">Lead intake</span>
          <h2>Add a lead</h2>
        </div>
        <span className="flow-status">
          <i /> AI context analysis · deterministic scoring safeguards
        </span>
      </div>
      <p className="intake-form-note">
        All fields are optional. Enter at least one detail you know; leave the rest blank.
      </p>
      <div className="form-grid">
        <Field
          label="Full name"
          value={form.full_name}
          onChange={(value) => set("full_name", value)}
        />
        <Field
          label="Email"
          type="email"
          value={form.email}
          onChange={(value) => set("email", value)}
        />
        <Field
          label="Company"
          value={form.company_name}
          onChange={(value) => set("company_name", value)}
        />
        <Field
          label="Title"
          value={form.job_title || ""}
          onChange={(value) => set("job_title", value)}
        />
        <Field
          label="Industry"
          value={form.industry || ""}
          onChange={(value) => set("industry", value)}
        />
        <Field
          label="Geography"
          value={form.geography || ""}
          onChange={(value) => set("geography", value)}
        />
        <Field
          label="Employees"
          type="number"
          value={form.employee_count ?? ""}
          onChange={(value) =>
            set("employee_count", value ? Number(value) : null)
          }
        />
        <Field
          label="Budget"
          type="number"
          value={form.budget ?? ""}
          onChange={(value) => set("budget", value ? Number(value) : null)}
        />
        <Field
          label="Service"
          value={form.product_or_service}
          onChange={(value) => set("product_or_service", value)}
        />
      </div>
      <label className="field wide">
        <span>Inquiry / process context</span>
        <small className="inquiry-context-hint">
          Paste the original inquiry or summarize steps taken and where the person is in the
          process. AI treats this as context, not necessarily a verbatim quote.
        </small>
        <textarea
          value={form.message}
          onChange={(event) => set("message", event.target.value)}
          rows={5}
          placeholder="Customer need, steps taken, current stage, open questions, timing, or a short summary…"
        />
      </label>
      {error && <p className="form-error">{error}</p>}
      <button className="primary-button" disabled={busy}>
        {busy ? "Analyzing with AI…" : "Analyze and create lead"}
      </button>
    </form>
  );
}

const LeadTableRow = memo(function LeadTableRow({
  lead,
  isNew,
  selected,
  checked,
  canEditGenerated,
  canManageRouting,
  assignmentOptions,
  assignmentOptionsLoading,
  assignmentOptionsError,
  savingAssignment,
  savingGenerated,
  onToggle,
  onInspect,
  onAssignmentChange,
  onGeneratedChange,
}: {
  lead: Lead;
  isNew: boolean;
  selected: boolean;
  checked: boolean;
  canEditGenerated: boolean;
  canManageRouting: boolean;
  assignmentOptions: LeadAssignmentOption[];
  assignmentOptionsLoading: boolean;
  assignmentOptionsError: boolean;
  savingAssignment: boolean;
  savingGenerated: boolean;
  onToggle: (id: string) => void;
  onInspect: (id: string) => void;
  onAssignmentChange: (leadId: string, assignedRepId: string | null) => void;
  onGeneratedChange: (leadId: string, isGenerated: boolean) => void;
}) {
  const origin = crmOrigin(lead);
  return (
    <tr
      className={selected ? "selected" : ""}
      onClick={() => onInspect(lead.id)}
      tabIndex={0}
      onKeyDown={(event) => {
        if ((event.target as HTMLElement).closest("button, input, select")) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onInspect(lead.id);
        }
      }}
    >
      <td className="lead-select-cell">
        <input
          type="checkbox"
          aria-label={`Select ${lead.full_name}`}
          checked={checked}
          onChange={() => onToggle(lead.id)}
          onClick={(event) => event.stopPropagation()}
        />
      </td>
      <td className="lead-identity-cell">
        <LeadRowIdentity lead={lead} isNew={isNew} />
      </td>
      <td data-label="Source">
        <span
          className={`source-tag source-${origin?.toLowerCase() || leadSourceCategory(lead.source, lead.source_detail)}`}
        >
          {origin || leadSourceLabel(lead.source, lead.source_detail)}
        </span>
        <small>
          {origin ? "CRM import" : leadSourceDetailLabel(lead.source_detail)}
        </small>
      </td>
      <td data-label="Score">
        <div
          className="qualification-cell"
          aria-label={`Score ${lead.qualification?.total_score ?? "unavailable"}; ${lead.analysis?.requires_manual_review ? "Manual Review" : qualificationLabel(lead.qualification?.classification || "pending")}`}
        >
          <b>{lead.qualification?.total_score ?? "—"}</b>
          <QualificationBadge
            value={lead.qualification?.classification || "pending"}
            manualReview={lead.analysis?.requires_manual_review}
          />
        </div>
      </td>
      <td data-label="Routing">
        {pretty(lead.routing?.assigned_queue || "Pending")}
      </td>
      <td data-label="Representative">
        {canManageRouting ? (
          lead.routing ? (
            <select
              className="lead-rep-select"
              aria-label={`Assigned representative for ${lead.full_name}`}
              value={lead.routing.assigned_rep_id || ""}
              disabled={
                savingAssignment ||
                assignmentOptionsLoading ||
                assignmentOptionsError
              }
              onClick={(event) => event.stopPropagation()}
              onChange={(event) =>
                onAssignmentChange(lead.id, event.target.value || null)
              }
            >
              <option value="">Unassigned</option>
              {lead.routing.assigned_rep_id &&
                !assignmentOptions.some(
                  (option) => option.user_id === lead.routing?.assigned_rep_id,
                ) && (
                  <option value={lead.routing.assigned_rep_id}>
                    {lead.routing.assigned_rep_name || "Current representative"}
                  </option>
                )}
              {assignmentOptions.map((option) => (
                <option key={option.user_id} value={option.user_id}>
                  {option.display_name}
                </option>
              ))}
            </select>
          ) : (
            <span>Routing pending</span>
          )
        ) : (
          lead.routing?.assigned_rep_name || "Unassigned"
        )}
      </td>
      <td className="lead-generated-cell" data-label="Lead generated">
        <input
          type="checkbox"
          aria-label={`Lead generated for ${lead.full_name}`}
          checked={lead.is_generated}
          disabled={!canEditGenerated || savingGenerated}
          onClick={(event) => event.stopPropagation()}
          onChange={(event) =>
            onGeneratedChange(lead.id, event.target.checked)
          }
        />
      </td>
      <td data-label="Received">
        {new Date(lead.received_at).toLocaleDateString()}
      </td>
    </tr>
  );
});

function Field({
  label,
  value,
  onChange,
  type = "text",
}: {
  label: string;
  value: string | number;
  onChange: (value: string) => void;
  type?: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}
