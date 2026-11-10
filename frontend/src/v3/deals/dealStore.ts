import { useSyncExternalStore } from "react";
import { quoteTotals } from "./quoteMath";
import type { Lead } from "../../types";
import { getActiveWorkspace, request } from "../../api";

/**
 * Deal desk storage.
 *
 * Server records are authoritative; local storage is a recovery cache for
 * unsaved edits. Writes are serialized and use per-record optimistic versions.
 */

export type DealStage = "new" | "contacted" | "engaged" | "quoted" | "negotiation" | "contract" | "won" | "lost";

export type ProgressStage = "contacted" | "responded" | "meeting_scheduled" | "proposal_sent" | "converted" | "lost";

export type StageInfo = {
  id: DealStage;
  label: string;
  verb: string;
  probability: number;
  /** The progress stage the API records for this move, when it has one. */
  backend: ProgressStage[] | null;
  hint: string;
};

export const DEAL_STAGES: StageInfo[] = [
  { id: "new", label: "New", verb: "Mark as new", probability: 0.05, backend: null, hint: "Arrived and qualified; nobody has reached out yet." },
  { id: "contacted", label: "Contacted", verb: "Mark contacted", probability: 0.1, backend: ["contacted"], hint: "First touch made by call, email or message." },
  { id: "engaged", label: "Engaged", verb: "Mark engaged", probability: 0.25, backend: ["responded", "meeting_scheduled"], hint: "They replied or a meeting is booked." },
  { id: "quoted", label: "Quoted", verb: "Mark quoted", probability: 0.45, backend: ["proposal_sent"], hint: "A quotation or proposal is with them." },
  { id: "negotiation", label: "Negotiation", verb: "Move to negotiation", probability: 0.65, backend: null, hint: "Scope, price or terms are being worked out." },
  { id: "contract", label: "Contract & SLA", verb: "Move to contract", probability: 0.85, backend: null, hint: "Terms agreed; service levels and signature pending." },
  { id: "won", label: "Won", verb: "Record a win", probability: 1, backend: ["converted"], hint: "Signed. Revenue is recorded." },
  { id: "lost", label: "Lost", verb: "Record as lost", probability: 0, backend: ["lost"], hint: "Closed without a deal." },
];

export const OPEN_STAGES = DEAL_STAGES.filter((stage) => stage.id !== "won" && stage.id !== "lost");
export const stageInfo = (id: DealStage) => DEAL_STAGES.find((stage) => stage.id === id) ?? DEAL_STAGES[0];
export const stageIndex = (id: DealStage) => DEAL_STAGES.findIndex((stage) => stage.id === id);

/** Maps the API's derived sales stage onto the deal desk's columns. */
export function stageFromBackend(stage: string | null | undefined): DealStage | null {
  switch (stage) {
    case "new_lead":
    case "follow_up_drafted":
      return "new";
    case "contacted":
    case "follow_up_sent":
      return "contacted";
    case "responded":
    case "meeting_scheduled":
      return "engaged";
    case "opportunity":
    case "proposal":
    case "proposal_sent":
      return "quoted";
    case "negotiation":
      return "negotiation";
    case "contract":
      return "contract";
    case "closed_won":
      return "won";
    case "closed_lost":
      return "lost";
    default:
      return null;
  }
}

/* ------------------------------------------------------------------ Types */

export type Billing = "one_time" | "monthly";

export type CatalogItem = {
  id: string;
  name: string;
  description: string;
  unitPrice: number;
  billing: Billing;
  unit: string;
};

export type QuoteLine = {
  id: string;
  name: string;
  description: string;
  quantity: number;
  unitPrice: number;
  billing: Billing;
  unit: string;
  discountPct: number;
};

export type QuoteStatus = "draft" | "sent" | "accepted" | "declined" | "superseded";

export type Quote = {
  id: string;
  number: string;
  version: number;
  status: QuoteStatus;
  currency: string;
  lines: QuoteLine[];
  discountPct: number;
  taxPct: number;
  termMonths: number;
  validUntil: string;
  notes: string;
  createdAt: string;
  sentAt: string | null;
  decidedAt: string | null;
};

export type NegotiationKind = "offer" | "counter" | "concession" | "objection" | "agreement" | "note";

export type NegotiationEntry = {
  id: string;
  at: string;
  party: "us" | "client";
  kind: NegotiationKind;
  amount: number | null;
  summary: string;
};

export type SlaStatus = "draft" | "proposed" | "agreed" | "signed";

export type SlaTerms = {
  status: SlaStatus;
  responseHours: number;
  resolutionHours: number;
  uptimePct: number;
  supportWindow: string;
  termMonths: number;
  paymentTerms: string;
  startDate: string;
  renewal: "auto" | "manual";
  signedBy: string;
  signedAt: string | null;
  notes: string;
};

export type DealActivity = { id: string; at: string; text: string; kind: "stage" | "quote" | "negotiation" | "sla" | "value" | "note" };

export type Deal = {
  leadId: string;
  stage: DealStage | null;
  stageChangedAt: string | null;
  valueOverride: number | null;
  expectedClose: string | null;
  generatedBy: { id: string; name: string } | null;
  quotes: Quote[];
  negotiation: NegotiationEntry[];
  sla: SlaTerms | null;
  activity: DealActivity[];
  updatedAt: string;
};

type DealsState = {
  version: 1;
  deals: Record<string, Deal>;
  catalog: CatalogItem[];
  monthlyGoal: number | null;
  quoteCounter: number;
  syncStatus: "loading" | "saving" | "saved" | "error";
  syncError: string | null;
  dirty: boolean;
};

/* ---------------------------------------------------------------- Catalog */

export const DEFAULT_CATALOG: CatalogItem[] = [
  { id: "svc-discovery", name: "Discovery & solution design", description: "Workshops, process mapping and a written architecture.", unitPrice: 2500, billing: "one_time", unit: "engagement" },
  { id: "svc-agent", name: "Agentic AI build", description: "Custom AI agent with tools, guardrails and evaluation.", unitPrice: 12000, billing: "one_time", unit: "project" },
  { id: "svc-workflow", name: "Workflow automation", description: "End-to-end automation of one business workflow.", unitPrice: 4500, billing: "one_time", unit: "workflow" },
  { id: "svc-rag", name: "RAG knowledge pipeline", description: "Ingestion, retrieval and answer quality tuning on your data.", unitPrice: 8000, billing: "one_time", unit: "pipeline" },
  { id: "svc-chatbot", name: "AI chatbot", description: "Website or WhatsApp assistant connected to your systems.", unitPrice: 5500, billing: "one_time", unit: "bot" },
  { id: "svc-integration", name: "API integration", description: "Two-way integration between two systems.", unitPrice: 3000, billing: "one_time", unit: "integration" },
  { id: "svc-retainer", name: "Managed operations retainer", description: "Monitoring, fixes and monthly improvements.", unitPrice: 1800, billing: "monthly", unit: "month" },
  { id: "svc-hours", name: "Engineering hours", description: "Ad-hoc engineering at the agreed rate.", unitPrice: 45, billing: "one_time", unit: "hour" },
];

/* ------------------------------------------------------------------ Store */

const KEY_PREFIX = "corral-deals:v1:";
let workspaceKey = "";
let activeId = "";
let canWrite = false;
let canManage = false;
let ready = false;
let versions: Record<string, number> = {};
let settingsVersion = 0;
let saveSequence = 0;
let pending: Promise<void> = Promise.resolve();
let loadPromise: Promise<void> = Promise.resolve();
let loadFailures = 0;
let loadRetryTimer: ReturnType<typeof setTimeout> | null = null;
let state: DealsState = emptyState();
const listeners = new Set<() => void>();

function emptyState(): DealsState {
  return { version: 1, deals: {}, catalog: DEFAULT_CATALOG, monthlyGoal: null, quoteCounter: 0, syncStatus: "loading", syncError: null, dirty: false };
}

function read(key: string): DealsState {
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return emptyState();
    const parsed = JSON.parse(raw) as Partial<DealsState> & { baseVersions?: Record<string, number>; baseSettingsVersion?: number };
    versions = parsed.baseVersions ?? {};
    settingsVersion = parsed.baseSettingsVersion ?? 0;
    if (parsed?.version !== 1) return emptyState();
    return {
      version: 1,
      deals: parsed.deals && typeof parsed.deals === "object" ? parsed.deals : {},
      catalog: Array.isArray(parsed.catalog) && parsed.catalog.length ? parsed.catalog : DEFAULT_CATALOG,
      monthlyGoal: typeof parsed.monthlyGoal === "number" ? parsed.monthlyGoal : null,
      quoteCounter: typeof parsed.quoteCounter === "number" ? parsed.quoteCounter : 0,
      syncStatus: "loading", syncError: parsed.dirty && typeof parsed.syncError === "string" ? parsed.syncError : null,
      dirty: parsed.dirty === true,
    };
  } catch {
    return emptyState();
  }
}

function persist() {
  if (!workspaceKey) return;
  try {
    window.localStorage.setItem(workspaceKey, JSON.stringify({ ...state, baseVersions: versions, baseSettingsVersion: settingsVersion }));
  } catch {
    /* Storage can be unavailable (private mode, quota). The session keeps working in memory. */
  }
}

function emit() {
  listeners.forEach((listener) => listener());
}

/** Point the store at a workspace. Safe to call on every render. */
export function bindDealWorkspace(workspaceId: string, editable = false, manageable = false) {
  const key = workspaceId ? `${KEY_PREFIX}${workspaceId}` : "";
  if (key === workspaceKey) { canWrite = editable; canManage = manageable; return; }
  workspaceKey = key;
  activeId = workspaceId;
  canWrite = editable;
  canManage = manageable;
  ready = false;
  if (loadRetryTimer) clearTimeout(loadRetryTimer);
  loadRetryTimer = null;
  loadFailures = 0;
  versions = {};
  settingsVersion = 0;
  saveSequence = 0;
  pending = Promise.resolve();
  state = key ? read(key) : emptyState();
  emit();
  if (key) {
    let migrateLegacy = false;
    try { migrateLegacy = !JSON.parse(window.localStorage.getItem(key) ?? "{}").syncStatus; } catch { /* No legacy cache. */ }
    loadPromise = refreshDeals({ migrateLegacy });
  }
}

type ServerDeals = { deals: Record<string, { version: number; deal: Deal }>; settings: { version: number; catalog: CatalogItem[]; monthlyGoal: number | null; quoteCounter: number } | null };

function syncFailure(reason: unknown) {
  state = { ...state, syncStatus: "error", syncError: reason instanceof Error ? reason.message : "Deal changes could not be saved." };
  persist(); emit();
}

export async function refreshDeals({ migrateLegacy = false, discardDraft = false } = {}) {
  const id = activeId;
  const sequence = saveSequence;
  try {
    const server = await request<ServerDeals>("/v1/deals");
    if (id !== activeId || sequence !== saveSequence) return;
    loadFailures = 0;
    if (loadRetryTimer) clearTimeout(loadRetryTimer);
    loadRetryTimer = null;
    const cached = state;
    const remote = Object.fromEntries(Object.entries(server.deals).map(([leadId, row]) => [leadId, row.deal]));
    const cachedMatchesServer = JSON.stringify(cached.deals) === JSON.stringify(remote)
      && (!server.settings || (JSON.stringify(cached.catalog) === JSON.stringify(server.settings.catalog.length ? server.settings.catalog : DEFAULT_CATALOG)
        && cached.monthlyGoal === server.settings.monthlyGoal
        && cached.quoteCounter === server.settings.quoteCounter));
    if (cached.dirty && !discardDraft && !cachedMatchesServer) {
      ready = true;
      state = { ...cached, syncStatus: "error", syncError: cached.syncError ?? "An unsaved deal edit needs another save attempt." }; emit(); return;
    }
    versions = Object.fromEntries(Object.entries(server.deals).map(([leadId, row]) => [leadId, row.version]));
    settingsVersion = server.settings?.version ?? 0;
    state = { ...state, deals: remote, ...(server.settings ? { catalog: server.settings.catalog.length ? server.settings.catalog : DEFAULT_CATALOG, monthlyGoal: server.settings.monthlyGoal, quoteCounter: server.settings.quoteCounter } : {}), syncStatus: "saved", syncError: null, dirty: false };
    ready = true;
    // Migrate the old device-only records once; never replace a saved server record.
    for (const [leadId, deal] of Object.entries(cached.deals)) {
      if (migrateLegacy && !remote[leadId] && canWrite) { state = { ...state, deals: { ...state.deals, [leadId]: deal } }; queueDeal(leadId, deal); }
    }
    if (migrateLegacy && !server.settings && canManage && (cached.monthlyGoal != null || JSON.stringify(cached.catalog) !== JSON.stringify(DEFAULT_CATALOG))) queueSettings();
    persist(); emit();
  } catch (reason) {
    if (id !== activeId) return;
    if (state.dirty) { syncFailure(reason); return; }
    if (loadFailures < 3) {
      loadFailures += 1;
      if (loadRetryTimer) clearTimeout(loadRetryTimer);
      loadRetryTimer = setTimeout(() => { void refreshDeals({ migrateLegacy, discardDraft }); }, 1500 * loadFailures);
      return;
    }
    state = { ...state, syncStatus: "error", syncError: reason instanceof Error ? reason.message : "Deal records could not be loaded.", dirty: false };
    emit();
  }
}

function enqueue(save: () => Promise<void>) {
  const id = activeId;
  const sequence = ++saveSequence;
  state = { ...state, syncStatus: "saving", syncError: null, dirty: true }; persist(); emit();
  const task = pending.then(async () => {
    if (id !== activeId || id !== getActiveWorkspace()) return;
    await save();
    if (id === activeId && sequence === saveSequence) { state = { ...state, syncStatus: "saved", syncError: null, dirty: false }; persist(); emit(); }
  });
  pending = task;
  // Report failed persistence without creating an unhandled rejection. Subsequent
  // writes remain blocked until a reload resolves a possible version conflict.
  void task.catch((reason) => { if (id === activeId) syncFailure(reason); });
}

function queueDeal(leadId: string, deal: Deal) {
  const id = activeId;
  enqueue(async () => {
    const result = await request<{ version: number }>(`/v1/leads/${leadId}/deal`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ version: versions[leadId] ?? 0, deal: { ...deal, quotes: deal.quotes.map((quote) => ({ ...quote, validUntil: quote.validUntil || null })), sla: deal.sla ? { ...deal.sla, startDate: deal.sla.startDate || null } : null } }),
    }, { activityToast: false });
    if (id === activeId) versions[leadId] = result.version;
  });
}

export async function flushDealChanges() { await loadPromise; await pending; if (state.syncStatus === "error") throw new Error(state.syncError ?? "Deal changes were not saved."); }

export async function reloadDealRecords() {
  await pending.catch(() => undefined);
  pending = Promise.resolve();
  await refreshDeals({ discardDraft: true });
}

/** Retry the kept draft using the last acknowledged versions; conflicts stay explicit. */
export async function retryDealChanges() {
  if (!state.dirty) { loadFailures = 0; await refreshDeals(); return; }
  await pending.catch(() => undefined);
  pending = Promise.resolve();
  if (!ready) {
    await refreshDeals({ discardDraft: true });
    return;
  }
  if (canWrite) {
    for (const [leadId, deal] of Object.entries(state.deals)) queueDeal(leadId, deal);
  }
  if (canManage) queueSettings();
  await pending.catch(() => undefined);
}

export function pollDealRecords() {
  if (activeId && ready && state.syncStatus === "saved") return refreshDeals();
}

function queueSettings() {
  const id = activeId;
  const snapshot = { catalog: state.catalog, monthlyGoal: state.monthlyGoal, quoteCounter: state.quoteCounter };
  enqueue(async () => {
    const result = await request<{ version: number }>("/v1/deals/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...snapshot, version: settingsVersion }) }, { activityToast: false });
    if (id === activeId) settingsVersion = result.version;
  });
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function update(mutator: (draft: DealsState) => DealsState) {
  if (!ready || state.syncStatus === "error") return;
  const previous = state;
  const next = mutator(state);
  const dealChanged = Object.entries(next.deals).some(([leadId, deal]) => previous.deals[leadId] !== deal);
  const settingsChanged = previous.catalog !== next.catalog || previous.monthlyGoal !== next.monthlyGoal;
  if ((dealChanged && !canWrite) || (settingsChanged && !canManage)) {
    syncFailure(new Error("Your workspace role does not allow this change.")); return;
  }
  state = next;
  persist();
  emit();
  for (const [leadId, deal] of Object.entries(state.deals)) {
    if (previous.deals[leadId] !== deal && canWrite) queueDeal(leadId, deal);
  }
  if (canManage && (previous.catalog !== state.catalog || previous.monthlyGoal !== state.monthlyGoal)) {
    queueSettings();
  }
}

export function useDeals() {
  return useSyncExternalStore(subscribe, () => state, () => state);
}

const now = () => new Date().toISOString();
export const newId = (prefix: string) =>
  `${prefix}-${typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID().slice(0, 8) : Math.random().toString(36).slice(2, 10)}`;

export function blankDeal(leadId: string): Deal {
  return {
    leadId,
    stage: null,
    stageChangedAt: null,
    valueOverride: null,
    expectedClose: null,
    generatedBy: null,
    quotes: [],
    negotiation: [],
    sla: null,
    activity: [],
    updatedAt: now(),
  };
}

export function getDeal(leadId: string): Deal {
  return state.deals[leadId] ?? blankDeal(leadId);
}

function patchDeal(leadId: string, change: (deal: Deal) => Deal) {
  update((current) => {
    const deal = current.deals[leadId] ?? blankDeal(leadId);
    return { ...current, deals: { ...current.deals, [leadId]: { ...change(deal), updatedAt: now() } } };
  });
}

function log(deal: Deal, kind: DealActivity["kind"], text: string): DealActivity[] {
  return [{ id: newId("act"), at: now(), kind, text }, ...deal.activity].slice(0, 80);
}

/* ---------------------------------------------------------------- Actions */

export const dealActions = {
  setStage(leadId: string, stage: DealStage | null, note?: string) {
    patchDeal(leadId, (deal) => ({
      ...deal,
      stage,
      stageChangedAt: now(),
      activity: stage ? log(deal, "stage", `Moved to ${stageInfo(stage).label}${note ? ` — ${note}` : ""}`) : deal.activity,
    }));
  },
  setValue(leadId: string, value: number | null) {
    patchDeal(leadId, (deal) => ({ ...deal, valueOverride: value, activity: log(deal, "value", value === null ? "Deal value cleared" : `Deal value set to ${formatMoney(value)}`) }));
  },
  setExpectedClose(leadId: string, date: string | null) {
    patchDeal(leadId, (deal) => ({ ...deal, expectedClose: date }));
  },
  setGeneratedBy(leadId: string, member: { id: string; name: string } | null) {
    patchDeal(leadId, (deal) => ({ ...deal, generatedBy: member, activity: log(deal, "note", member ? `Lead generated by ${member.name}` : "Lead generator cleared") }));
  },
  saveQuote(leadId: string, quote: Quote) {
    patchDeal(leadId, (deal) => {
      const exists = deal.quotes.some((item) => item.id === quote.id);
      return {
        ...deal,
        quotes: exists ? deal.quotes.map((item) => (item.id === quote.id ? quote : item)) : [quote, ...deal.quotes],
        activity: exists ? deal.activity : log(deal, "quote", `Quote ${quote.number} drafted · ${formatMoney(quoteTotals(quote).contractValue, quote.currency)}`),
      };
    });
  },
  newQuoteNumber() {
    let number = "";
    update((current) => {
      const next = current.quoteCounter + 1;
      number = `Q-${new Date().getFullYear()}-${String(next).padStart(4, "0")}-${newId("q").slice(2, 6).toUpperCase()}`;
      return { ...current, quoteCounter: next };
    });
    return number;
  },
  setQuoteStatus(leadId: string, quoteId: string, status: QuoteStatus) {
    patchDeal(leadId, (deal) => {
      const target = deal.quotes.find((item) => item.id === quoteId);
      const quotes = deal.quotes.map((item) => {
        if (item.id === quoteId) {
          return {
            ...item,
            status,
            sentAt: status === "sent" ? item.sentAt ?? now() : item.sentAt,
            decidedAt: status === "accepted" || status === "declined" ? now() : item.decidedAt,
          };
        }
        // A newly sent or accepted version supersedes earlier open versions.
        if ((status === "sent" || status === "accepted") && (item.status === "sent" || item.status === "draft")) {
          return { ...item, status: "superseded" as const };
        }
        return item;
      });
      const label = { draft: "returned to draft", sent: "marked as sent", accepted: "accepted", declined: "declined", superseded: "superseded" }[status];
      return { ...deal, quotes, activity: log(deal, "quote", `Quote ${target?.number ?? ""} ${label}`) };
    });
  },
  deleteQuote(leadId: string, quoteId: string) {
    patchDeal(leadId, (deal) => ({ ...deal, quotes: deal.quotes.filter((item) => item.id !== quoteId) }));
  },
  addNegotiation(leadId: string, entry: Omit<NegotiationEntry, "id" | "at">) {
    patchDeal(leadId, (deal) => ({
      ...deal,
      negotiation: [{ ...entry, id: newId("neg"), at: now() }, ...deal.negotiation],
      activity: log(deal, "negotiation", `${entry.party === "us" ? "We" : "Client"}: ${NEGOTIATION_LABELS[entry.kind].toLowerCase()}${entry.amount != null ? ` at ${formatMoney(entry.amount)}` : ""}`),
    }));
  },
  removeNegotiation(leadId: string, entryId: string) {
    patchDeal(leadId, (deal) => ({ ...deal, negotiation: deal.negotiation.filter((item) => item.id !== entryId) }));
  },
  saveSla(leadId: string, sla: SlaTerms) {
    patchDeal(leadId, (deal) => ({
      ...deal,
      sla,
      activity: deal.sla?.status !== sla.status ? log(deal, "sla", `Service levels ${SLA_STATUS_LABELS[sla.status].toLowerCase()}`) : deal.activity,
    }));
  },
  saveCatalog(catalog: CatalogItem[]) {
    update((current) => ({ ...current, catalog }));
  },
  setMonthlyGoal(goal: number | null) {
    update((current) => ({ ...current, monthlyGoal: goal }));
  },
};

export const NEGOTIATION_LABELS: Record<NegotiationKind, string> = {
  offer: "Offer",
  counter: "Counter-offer",
  concession: "Concession",
  objection: "Objection",
  agreement: "Agreement",
  note: "Note",
};

export const SLA_STATUS_LABELS: Record<SlaStatus, string> = {
  draft: "Drafted",
  proposed: "Proposed",
  agreed: "Agreed",
  signed: "Signed",
};

export function defaultSla(): SlaTerms {
  const start = new Date();
  start.setDate(start.getDate() + 14);
  return {
    status: "draft",
    responseHours: 4,
    resolutionHours: 24,
    uptimePct: 99.5,
    supportWindow: "Mon–Fri, 9:00–18:00",
    termMonths: 12,
    paymentTerms: "50% upfront, 50% on delivery",
    startDate: start.toISOString().slice(0, 10),
    renewal: "auto",
    signedBy: "",
    signedAt: null,
    notes: "",
  };
}

/* ------------------------------------------------------------ Calculation */

export { lineTotal, quoteTotals } from "./quoteMath";

/** The quote that represents the deal now: accepted, else the newest sent, else the newest draft. */
export function currentQuote(deal: Deal): Quote | null {
  return (
    deal.quotes.find((quote) => quote.status === "accepted") ??
    deal.quotes.find((quote) => quote.status === "sent") ??
    deal.quotes.find((quote) => quote.status === "draft") ??
    null
  );
}

export type DealView = {
  lead: Lead;
  deal: Deal;
  stage: DealStage;
  /** Where the stage came from, so the interface can say which facts are recorded and which are local. */
  stageSource: "outcome" | "workspace" | "recorded" | "default";
  value: number | null;
  valueSource: "won" | "override" | "quote" | "opportunity" | null;
  weighted: number | null;
  owner: string | null;
  ownerId: string | null;
  daysInStage: number | null;
  quote: Quote | null;
  slaDue: { at: string; overdue: boolean; minutes: number } | null;
};

export function viewDeal(lead: Lead, deal: Deal | undefined, recordedStage?: string | null, observedWinRate: number | null = null): DealView {
  const record = deal ?? blankDeal(lead.id);
  const outcome = lead.outcome?.pipeline_stage;
  let stage: DealStage = "new";
  let stageSource: DealView["stageSource"] = "default";
  if (outcome === "closed_won" || outcome === "closed_lost") {
    stage = outcome === "closed_won" ? "won" : "lost";
    stageSource = "outcome";
  } else if (record.stage && record.stage !== "won" && record.stage !== "lost") {
    stage = record.stage;
    stageSource = "workspace";
  } else {
    const recorded = stageFromBackend(recordedStage) ?? stageFromBackend(outcome);
    if (recorded) {
      stage = recorded;
      stageSource = "recorded";
    }
  }
  const quote = currentQuote(record);
  let value: number | null = null;
  let valueSource: DealView["valueSource"] = null;
  if (stage === "won" && lead.outcome?.closed_revenue != null) {
    value = lead.outcome.closed_revenue;
    valueSource = "won";
  } else if (record.valueOverride != null) {
    value = record.valueOverride;
    valueSource = "override";
  } else if (quote && quote.currency === "USD") {
    value = quoteTotals(quote).contractValue;
    valueSource = "quote";
  } else if (quote) {
    value = null;
  } else if (lead.outcome?.opportunity_value != null && lead.outcome.opportunity_value > 0) {
    value = lead.outcome.currency === "USD" ? lead.outcome.opportunity_value : null;
    valueSource = value == null ? null : "opportunity";
  }
  const changed = record.stageChangedAt ?? (stageSource === "outcome" ? lead.outcome?.closed_at ?? null : null) ?? lead.received_at;
  const daysInStage = changed ? Math.max(0, Math.floor((Date.now() - Date.parse(changed)) / 86_400_000)) : null;
  const slaAt = lead.routing?.sla_target_at ?? null;
  const minutes = slaAt ? Math.round((Date.parse(slaAt) - Date.now()) / 60_000) : 0;
  return {
    lead,
    deal: record,
    stage,
    stageSource,
    value,
    valueSource,
    weighted: value != null && observedWinRate != null
      ? Math.round(value * 100 * observedWinRate) / 100 : null,
    owner: lead.routing?.assigned_rep_name ?? null,
    ownerId: lead.routing?.assigned_rep_id ?? null,
    daysInStage,
    quote,
    slaDue: slaAt && stage === "new" ? { at: slaAt, overdue: minutes < 0, minutes } : null,
  };
}

/* ------------------------------------------------------------- Formatting */

export function formatMoney(value: number, currency = "USD", compact = false) {
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      // Whole amounts read as $189,000; cents appear only when there are cents.
      minimumFractionDigits: compact || Number.isInteger(value) ? 0 : 2,
      maximumFractionDigits: compact ? (Math.abs(value) >= 1000 ? 1 : 0) : 2,
      notation: compact && Math.abs(value) >= 10_000 ? "compact" : "standard",
    }).format(value);
  } catch {
    return `${currency} ${compact ? Math.round(value).toLocaleString() : value.toFixed(2)}`;
  }
}

export function formatDuration(minutes: number) {
  const abs = Math.abs(minutes);
  if (abs < 60) return `${abs}m`;
  if (abs < 60 * 24) return `${Math.round(abs / 60)}h`;
  return `${Math.round(abs / 1440)}d`;
}
