import { useEffect, useMemo, useState } from "react";
import { getLeadClosingOptions } from "../../api";
import type { Lead } from "../../types";
import {
  dealActions,
  DEAL_STAGES,
  defaultSla,
  formatMoney,
  flushDealChanges,
  lineTotal,
  NEGOTIATION_LABELS,
  newId,
  quoteTotals,
  SLA_STATUS_LABELS,
  stageIndex,
  stageInfo,
  useDeals,
  type Billing,
  type CatalogItem,
  type DealStage,
  type DealView,
  type NegotiationKind,
  type Quote,
  type QuoteLine,
  type SlaTerms,
} from "../deals/dealStore";
import { FeatureGuide } from "../../components/FeatureGuide";
import { StageMoveSheet } from "../deals/StageMoveSheet";
import { Icon } from "../ui/Icon";
import { Badge, Banner, Button, EmptyState, IconButton, Segmented, Sheet, formatShortDate } from "../ui/primitives";
import { confirmAction } from "../ui/confirm";

type Member = { user_id: string; display_name: string };

/* ============================================================ Stage rail */

/** The deal's journey as one control: where it is, and one click to move it. */
export function StageRail({ view, canEdit, onMove }: { view: DealView; canEdit: boolean; onMove: (target: DealStage) => void }) {
  const current = stageIndex(view.stage);
  const closedLost = view.stage === "lost";
  return (
    <nav className="d-rail" aria-label="Deal stage">
      <ol>
        {DEAL_STAGES.filter((stage) => stage.id !== "lost").map((stage, index) => {
          const state = closedLost ? "idle" : index < current ? "done" : index === current ? "current" : "todo";
          return (
            <li key={stage.id} data-state={state} data-stage={stage.id}>
              <button
                type="button"
                disabled={!canEdit || index === current}
                aria-current={state === "current" ? "step" : undefined}
                onClick={() => onMove(stage.id)}
                title={stage.hint}
              >
                <span className="d-rail-mark" aria-hidden="true">{state === "done" ? <Icon name="check" size={11} strokeWidth={2.4} /> : null}</span>
                <span className="d-rail-label">{stage.label}</span>
              </button>
            </li>
          );
        })}
      </ol>
      {closedLost && <Badge tone="danger">Closed · lost</Badge>}
      {!closedLost && canEdit && view.stage !== "won" && (
        <button type="button" className="d-rail-lost" onClick={() => onMove("lost")}>Mark lost</button>
      )}
    </nav>
  );
}

/* ============================================================== Deal room */

export function DealRoom({
  lead,
  view,
  canEdit: permittedToEdit,
  canEmail,
  onEmailQuote,
  onMoved,
  startNewQuote,
  onQuoteStarted,
}: {
  lead: Lead;
  view: DealView;
  canEdit: boolean;
  canEmail: boolean;
  onEmailQuote: (subject: string, body: string, quoteId: string) => void;
  onMoved: (lead: Lead | null, message: string) => void;
  startNewQuote?: boolean;
  onQuoteStarted?: () => void;
}) {
  const store = useDeals();
  const canEdit = permittedToEdit && store.syncStatus === "saved";
  const [members, setMembers] = useState<Member[]>([]);
  const [editing, setEditing] = useState<Quote | null>(null);
  const [reviewing, setReviewing] = useState<Quote | null>(null);
  const [move, setMove] = useState<DealStage | null>(null);
  const [valueDraft, setValueDraft] = useState<string | null>(null);
  const deal = view.deal;

  useEffect(() => {
    if (!canEdit) return;
    const controller = new AbortController();
    getLeadClosingOptions(controller.signal).then((rows) => { if (!controller.signal.aborted) setMembers(rows); }).catch(() => undefined);
    return () => controller.abort();
  }, [canEdit]);

  const createQuote = () => {
    const latest = deal.quotes[0];
    const number = latest ? latest.number : dealActions.newQuoteNumber();
    const valid = new Date();
    valid.setDate(valid.getDate() + 30);
    setEditing({
      id: newId("quote"),
      number,
      version: latest ? latest.version + 1 : 1,
      status: "draft",
      currency: "USD",
      lines: latest ? latest.lines.map((line) => ({ ...line, id: newId("line") })) : [],
      discountPct: latest?.discountPct ?? 0,
      taxPct: latest?.taxPct ?? 0,
      termMonths: latest?.termMonths ?? 12,
      validUntil: valid.toISOString().slice(0, 10),
      notes: latest?.notes ?? "",
      createdAt: new Date().toISOString(),
      sentAt: null,
      decidedAt: null,
    });
  };

  useEffect(() => {
    if (startNewQuote && canEdit) {
      createQuote();
      onQuoteStarted?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startNewQuote, canEdit]);

  const lastClient = deal.negotiation.find((entry) => entry.party === "client" && entry.amount != null);
  const lastOurs = deal.negotiation.find((entry) => entry.party === "us" && entry.amount != null);

  return (
    <div className="d-room">
      <div className="ops-actions"><FeatureGuide topic="deals" /></div>
      <section className="d-summary" aria-label="Deal summary">
        <div className="d-sum-cell d-sum-value">
          <span>Deal value</span>
          {valueDraft !== null ? (
            <form
              className="d-value-form"
              onSubmit={(event) => {
                event.preventDefault();
                dealActions.setValue(lead.id, valueDraft.trim() === "" ? null : Math.max(0, Number(valueDraft)));
                setValueDraft(null);
              }}
            >
              <input autoFocus type="number" inputMode="decimal" min="0" step="1" aria-label="Deal value in USD" value={valueDraft} onChange={(event) => setValueDraft(event.target.value)} onBlur={(event) => event.currentTarget.form?.requestSubmit()} onKeyDown={(event) => { if (event.key === "Escape") setValueDraft(null); }} />
            </form>
          ) : (
            <button type="button" className="d-value" disabled={!canEdit || view.valueSource === "won"} onClick={() => setValueDraft(deal.valueOverride != null ? String(deal.valueOverride) : view.value != null ? view.value.toFixed(2) : "")}>
              <b className="num">{view.value != null ? formatMoney(view.value) : "Add value"}</b>
              {canEdit && view.valueSource !== "won" && <Icon name="edit" size={13} />}
            </button>
          )}
          <small>{view.valueSource === "won" ? "Recorded at close" : view.valueSource === "quote" ? `From quote ${view.quote?.number}` : view.valueSource === "opportunity" ? "Recorded opportunity" : view.valueSource === "override" ? "Set by your team" : "No recorded value"}</small>
        </div>
        <div className="d-sum-cell">
          <span>Weighted</span>
          <b className="num">{view.weighted != null ? formatMoney(view.weighted) : "—"}</b>
          <small>{view.weighted == null ? "Not tracked: no recorded close rate" : "Recorded cohort win rate"}</small>
        </div>
        <label className="d-sum-cell">
          <span>Expected close</span>
          <input type="date" disabled={!canEdit} value={deal.expectedClose ?? ""} onChange={(event) => dealActions.setExpectedClose(lead.id, event.target.value || null)} aria-label="Expected close date" />
          <small>{deal.expectedClose ? relativeDays(deal.expectedClose) : "Not set"}</small>
        </label>
        <label className="d-sum-cell">
          <span>Lead generated by</span>
          <select
            className="v-popup"
            disabled={!canEdit}
            value={deal.generatedBy?.id ?? ""}
            onChange={(event) => {
              const member = members.find((item) => item.user_id === event.target.value);
              dealActions.setGeneratedBy(lead.id, member ? { id: member.user_id, name: member.display_name } : null);
            }}
            aria-label="Lead generated by"
          >
            <option value="">{lead.is_generated ? "Choose who sourced it" : "Inbound · not sourced"}</option>
            {deal.generatedBy && !members.some((member) => member.user_id === deal.generatedBy?.id) && <option value={deal.generatedBy.id}>{deal.generatedBy.name}</option>}
            {members.map((member) => <option key={member.user_id} value={member.user_id}>{member.display_name}</option>)}
          </select>
          <small>{lead.is_generated ? "Outbound lead" : "Arrived inbound"}</small>
        </label>
      </section>

      <div className="d-grid">
        <div className="d-main">
          {/* ------------------------------------------------------- Quotes */}
          <section className="v-card v-card-pad d-section" aria-labelledby="d-quotes">
            <header className="d-section-head">
              <span className="d-section-icon"><Icon name="quote" size={17} /></span>
              <div>
                <h2 id="d-quotes" className="v-card-title">Quotation</h2>
                <p className="v-card-sub">Price the work from your service catalogue. Every revision is kept.</p>
              </div>
              {canEdit && <Button kind={deal.quotes.length ? "gray" : "filled"} icon="plus" size="sm" onClick={createQuote}>{deal.quotes.length ? "New Version" : "Build Quote"}</Button>}
            </header>
            {deal.quotes.length === 0 ? (
              <EmptyState icon="quote" title="No quote yet">Add services from your catalogue, set terms and send it — the deal value follows the quote.</EmptyState>
            ) : (
              <ul className="d-quotes">
                {deal.quotes.map((quote) => {
                  const totals = quoteTotals(quote);
                  return (
                    <li key={quote.id} data-status={quote.status}>
                      <div className="d-quote-main">
                        <b>{quote.number} <small>v{quote.version}</small></b>
                        <span>{quote.lines.length} item{quote.lines.length === 1 ? "" : "s"} · {totals.monthly ? `${formatMoney(totals.oneTime)} + ${formatMoney(totals.monthly)}/mo` : formatMoney(totals.oneTime)} · valid to {formatShortDate(quote.validUntil)}</span>
                      </div>
                      <b className="d-quote-total num">{formatMoney(totals.contractValue)}</b>
                      <QuoteStatus status={quote.status} />
                      <div className="d-quote-actions">
                        <Button kind="plain" size="sm" onClick={() => setReviewing(quote)}>Review</Button>
                        <IconButton icon="download" label={`Print or save ${quote.number} as PDF`} size={16} onClick={() => printQuote(quote, lead)} />
                        {canEdit && quote.status === "draft" && <IconButton icon="edit" label={`Edit ${quote.number}`} size={16} onClick={() => setEditing(quote)} />}
                        {canEdit && quote.status === "draft" && !quote.sentAt && <IconButton icon="trash" label={`Delete draft ${quote.number}`} size={16} onClick={() => {
                          void confirmAction({ title: `Delete draft ${quote.number}?`, message: `Version ${quote.version} is removed. This can't be undone.`, confirmLabel: "Delete draft", tone: "danger" })
                            .then((confirmed) => { if (confirmed) dealActions.deleteQuote(lead.id, quote.id); });
                        }} />}
                        {canEmail && quote.status !== "superseded" && <IconButton icon="send" label={`Review and email ${quote.number}`} size={16} onClick={() => setReviewing(quote)} />}
                        {canEdit && quote.status !== "superseded" && (
                          <select
                            className="v-popup d-quote-status"
                            aria-label={`Status of ${quote.number}`}
                            value={quote.status}
                            onChange={(event) => dealActions.setQuoteStatus(lead.id, quote.id, event.target.value as Quote["status"])}
                          >
                            <option value="draft">Draft</option>
                            <option value="sent">Sent</option>
                            <option value="accepted">Accepted</option>
                            <option value="declined">Declined</option>
                          </select>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          {/* -------------------------------------------------- Negotiation */}
          <NegotiationLog lead={lead} canEdit={canEdit} quoteValue={view.quote ? quoteTotals(view.quote).contractValue : null} />
        </div>

        <aside className="d-side">
          <SlaCard lead={lead} sla={deal.sla} canEdit={canEdit} />
          <section className="v-card v-card-pad d-close" aria-labelledby="d-close">
            <h2 id="d-close" className="v-card-title">Close the deal</h2>
            {view.stage === "won" ? (
              <p className="d-closed" data-won><Icon name="trophy" size={18} />Won{lead.outcome?.closed_revenue != null ? ` · ${formatMoney(lead.outcome.closed_revenue)}` : ""}{lead.outcome?.credited_name ? ` · ${lead.outcome.credited_name}` : ""}</p>
            ) : view.stage === "lost" ? (
              <p className="d-closed"><Icon name="close" size={18} />Closed as lost</p>
            ) : (
              <>
                <p className="v-card-sub">
                  {lastClient && lastOurs
                    ? `Gap: ${formatMoney(Math.abs(lastOurs.amount! - lastClient.amount!))} between our last offer and theirs.`
                    : deal.sla?.status === "signed"
                      ? "Service levels are signed — record the win."
                      : "Record the outcome when it’s decided. Wins credit a team member and update revenue."}
                </p>
                {canEdit && (
                  <div className="d-close-actions">
                    <Button kind="filled" icon="trophy" onClick={() => setMove("won")}>Record Win</Button>
                    <Button kind="plain" onClick={() => setMove("lost")}>Lost</Button>
                  </div>
                )}
              </>
            )}
          </section>
          <section className="v-card v-card-pad d-activity" aria-labelledby="d-activity">
            <h2 id="d-activity" className="v-card-title">Deal activity</h2>
            {deal.activity.length ? (
              <ol>
                {deal.activity.slice(0, 8).map((entry) => (
                  <li key={entry.id}><span>{entry.text}</span><time dateTime={entry.at}>{formatShortDate(entry.at)}</time></li>
                ))}
              </ol>
            ) : <p className="v-card-sub">Quotes, offers and stage moves will show here.</p>}
            <p className="d-device-note"><Icon name="info" size={13} />Quotes, negotiation and service levels are saved to this workspace.</p>
          </section>
        </aside>
      </div>

      {editing && (
        <QuoteEditor
          quote={editing}
          catalog={store.catalog}
          lead={lead}
          onClose={() => setEditing(null)}
          onSave={async (quote, send) => {
            dealActions.saveQuote(lead.id, quote);
            await flushDealChanges();
            setEditing(null);
            if (send && canEmail) onEmailQuote(...quoteEmail(quote, lead), quote.id);
          }}
        />
      )}
      {reviewing && <Sheet title="Review quotation" subtitle={`${reviewing.number} · version ${reviewing.version}`} onClose={() => setReviewing(null)} width={880}
        footer={<><Button onClick={() => printQuote(reviewing, lead)} icon="download">Print / PDF</Button>
          {canEdit && reviewing.status === "draft" && <Button onClick={() => { setEditing(reviewing); setReviewing(null); }}>Edit quote</Button>}
          {canEmail && reviewing.status !== "superseded" && <Button kind="filled" icon="send" onClick={() => { onEmailQuote(...quoteEmail(reviewing, lead), reviewing.id); setReviewing(null); }}>Review email</Button>}</>}>
        <QuotePreview quote={reviewing} lead={lead} />
      </Sheet>}
      {move && (
        <StageMoveSheet
          view={view}
          target={move}
          onClose={() => setMove(null)}
          onMoved={(fresh, message) => { setMove(null); onMoved(fresh, message); }}
        />
      )}
    </div>
  );
}

function QuoteStatus({ status }: { status: Quote["status"] }) {
  const tone = status === "accepted" ? "strong" : status === "sent" ? "info" : status === "declined" ? "danger" : "neutral";
  return <Badge tone={tone}>{{ draft: "Draft", sent: "Sent", accepted: "Accepted", declined: "Declined", superseded: "Superseded" }[status]}</Badge>;
}

function relativeDays(date: string) {
  const days = Math.round((Date.parse(date) - Date.now()) / 86_400_000);
  if (days === 0) return "Today";
  if (days > 0) return `In ${days} day${days === 1 ? "" : "s"}`;
  return `${-days} day${days === -1 ? "" : "s"} ago`;
}

/* =========================================================== Quote editor */

function QuoteEditor({ quote, catalog, lead, onClose, onSave }: { quote: Quote; catalog: CatalogItem[]; lead: Lead; onClose: () => void; onSave: (quote: Quote, send: boolean) => Promise<void> }) {
  const [review, setReview] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const save = async (send: boolean) => {
    setSaving(true); setSaveError(null);
    try { await onSave(draft, send); }
    catch (error) { setSaveError(error instanceof Error ? error.message : "Could not save the quote."); }
    finally { setSaving(false); }
  };
  const [draft, setDraft] = useState<Quote>(quote);
  const totals = useMemo(() => quoteTotals(draft), [draft]);
  const hasMonthly = draft.lines.some((line) => line.billing === "monthly");
  const setLine = (id: string, patch: Partial<QuoteLine>) => setDraft((current) => ({ ...current, lines: current.lines.map((line) => (line.id === id ? { ...line, ...patch } : line)) }));
  const addFromCatalog = (item: CatalogItem) =>
    setDraft((current) => ({
      ...current,
      lines: [...current.lines, { id: newId("line"), name: item.name, description: item.description, quantity: 1, unitPrice: item.unitPrice, billing: item.billing, unit: item.unit, discountPct: 0 }],
    }));
  const addCustom = () =>
    setDraft((current) => ({ ...current, lines: [...current.lines, { id: newId("line"), name: "", description: "", quantity: 1, unitPrice: 0, billing: "one_time", unit: "item", discountPct: 0 }] }));
  const valid = draft.lines.length > 0 && draft.lines.every((line) => line.name.trim() && Number.isFinite(line.quantity) && line.quantity > 0 && Number.isFinite(line.unitPrice) && line.unitPrice >= 0 && line.discountPct >= 0 && line.discountPct <= 100)
    && draft.discountPct >= 0 && draft.discountPct <= 100 && draft.taxPct >= 0 && draft.taxPct <= 100 && draft.termMonths >= 1 && Number.isFinite(totals.contractValue);
  return (
    <Sheet
      title={`${draft.number} · version ${draft.version}`}
      subtitle={`Quotation for ${lead.company_name || lead.full_name}`}
      onClose={onClose}
      width={880}
      footer={
        <div className="q-foot">
          <div className="q-foot-total">
            <span>Contract value</span>
            <b className="num">{formatMoney(totals.contractValue)}</b>
          </div>
          <Button onClick={onClose}>Cancel</Button>
          {saveError && <span role="alert">{saveError}</span>}
          <Button disabled={!valid || saving} onClick={() => void save(false)}>Save Draft</Button>
          {review && <Button onClick={() => setReview(false)} disabled={saving}>Back to editing</Button>}
          <Button kind="filled" icon={review ? "send" : "quote"} disabled={!valid || saving} onClick={() => review ? void save(true) : setReview(true)}>{saving ? "Saving…" : review ? "Save & Review email" : "Review quotation"}</Button>
        </div>
      }
    >
      {review ? <QuotePreview quote={draft} lead={lead} /> : <div className="q-editor">
        <section className="q-catalog" aria-label="Service catalogue">
          <h3>Add from catalogue</h3>
          <ul>
            {catalog.map((item) => (
              <li key={item.id}>
                <button type="button" onClick={() => addFromCatalog(item)}>
                  <span><b>{item.name}</b><small>{formatMoney(item.unitPrice)} / {item.unit}{item.billing === "monthly" ? " · monthly" : ""}</small></span>
                  <Icon name="plus" size={15} />
                </button>
              </li>
            ))}
          </ul>
          <Button kind="plain" size="sm" icon="plus" onClick={addCustom}>Custom line</Button>
        </section>

        <section className="q-lines" aria-label="Quote lines">
          {draft.lines.length === 0 ? (
            <EmptyState icon="quote" title="Add the first item">Pick a service on the left or add a custom line.</EmptyState>
          ) : (
            <ul>
              {draft.lines.map((line) => (
                <li key={line.id} className="q-line">
                  <input aria-label="Item" className="q-line-item" value={line.name} placeholder="Item name" onChange={(event) => setLine(line.id, { name: event.target.value })} />
                  <input aria-label="Description" className="q-line-desc" value={line.description} placeholder="Short description" onChange={(event) => setLine(line.id, { description: event.target.value })} />
                  <label className="q-num q-qty"><span>Qty</span><input type="number" min="0" step="0.5" value={line.quantity} onChange={(event) => setLine(line.id, { quantity: Number(event.target.value) })} /></label>
                  <label className="q-num q-price"><span>Price</span><input type="number" min="0" step="1" value={line.unitPrice} onChange={(event) => setLine(line.id, { unitPrice: Number(event.target.value) })} /></label>
                  <label className="q-num q-disc"><span>Disc %</span><input type="number" min="0" max="100" step="1" value={line.discountPct} onChange={(event) => setLine(line.id, { discountPct: Number(event.target.value) })} /></label>
                  <label className="q-num q-billing"><span>Billing</span>
                    <select value={line.billing} onChange={(event) => setLine(line.id, { billing: event.target.value as Billing })}>
                      <option value="one_time">One-time</option>
                      <option value="monthly">Monthly</option>
                    </select>
                  </label>
                  <b className="q-line-total num">{formatMoney(lineTotal(line))}{line.billing === "monthly" ? <small>/mo</small> : null}</b>
                  <IconButton icon="trash" className="q-del" label={`Remove ${line.name || "line"}`} size={15} onClick={() => setDraft((current) => ({ ...current, lines: current.lines.filter((item) => item.id !== line.id) }))} />
                </li>
              ))}
            </ul>
          )}

          <div className="q-terms">
            <label className="v-field"><span>Quote discount %</span><input type="number" min="0" max="100" value={draft.discountPct} onChange={(event) => setDraft({ ...draft, discountPct: Number(event.target.value) })} /></label>
            <label className="v-field"><span>Tax %</span><input type="number" min="0" max="100" value={draft.taxPct} onChange={(event) => setDraft({ ...draft, taxPct: Number(event.target.value) })} /></label>
            <label className="v-field"><span>Term (months)</span><input type="number" min="1" max="120" value={draft.termMonths} disabled={!hasMonthly} onChange={(event) => setDraft({ ...draft, termMonths: Number(event.target.value) })} /></label>
            <label className="v-field"><span>Valid until</span><input type="date" value={draft.validUntil} onChange={(event) => setDraft({ ...draft, validUntil: event.target.value })} /></label>
          </div>
          <label className="v-field"><span>Notes &amp; assumptions</span><textarea rows={3} value={draft.notes} placeholder="Scope boundaries, timeline, what the client provides…" onChange={(event) => setDraft({ ...draft, notes: event.target.value })} /></label>

          <dl className="q-totals">
            <div><dt>One-time</dt><dd className="num">{formatMoney(totals.oneTime)}</dd></div>
            {hasMonthly && <div><dt>Monthly × {draft.termMonths}</dt><dd className="num">{formatMoney(totals.monthly)}/mo</dd></div>}
            {totals.discount > 0 && <div><dt>Discounts</dt><dd className="num">−{formatMoney(totals.discount)}</dd></div>}
            {draft.taxPct > 0 && <div><dt>Tax</dt><dd className="num">{formatMoney(totals.tax)}</dd></div>}
            <div className="q-grand"><dt>Contract value</dt><dd className="num">{formatMoney(totals.contractValue)}</dd></div>
          </dl>
        </section>
      </div>}
    </Sheet>
  );
}

function QuotePreview({ quote, lead }: { quote: Quote; lead: Lead }) {
  const totals = quoteTotals(quote);
  const money = (amount: number) => formatMoney(amount, quote.currency);
  return <article className="q-review" aria-label="Quotation review">
    <header><div><span className="v-card-sub">Prepared for</span><h2>{lead.company_name || lead.full_name}</h2><p>{lead.full_name}{lead.email ? ` · ${lead.email}` : ""}</p></div>
      <div><QuoteStatus status={quote.status} /><p>{quote.number} · v{quote.version}</p><p>{quote.validUntil ? `Valid until ${formatShortDate(quote.validUntil)}` : "No expiry date set"}</p></div></header>
    <div className="q-review-table"><table><thead><tr><th>Service</th><th>Quantity</th><th>Unit price</th><th>Discount</th><th>Amount</th></tr></thead><tbody>
      {quote.lines.map((line) => <tr key={line.id}><td><b>{line.name}</b>{line.description && <small>{line.description}</small>}<small>{line.billing === "monthly" ? `Monthly · ${quote.termMonths} months` : "One-time"}</small></td><td>{line.quantity}</td><td>{money(line.unitPrice)}</td><td>{line.discountPct}%</td><td>{money(lineTotal(line))}{line.billing === "monthly" ? "/month" : ""}</td></tr>)}
    </tbody></table></div>
    <dl className="q-totals"><div><dt>One-time after discounts</dt><dd>{money(totals.oneTime)}</dd></div>
      {totals.monthly > 0 && <div><dt>Monthly × {quote.termMonths}</dt><dd>{money(totals.monthly)}/month</dd></div>}
      <div><dt>Subtotal</dt><dd>{money(totals.subtotal)}</dd></div><div><dt>Tax ({quote.taxPct}%)</dt><dd>{money(totals.tax)}</dd></div>
      <div><dt>Total contract value</dt><dd><strong>{money(totals.contractValue)}</strong></dd></div></dl>
    {quote.notes && <section><h3>Notes & assumptions</h3><p className="q-review-notes">{quote.notes}</p></section>}
    <p className="v-card-sub">Review the recipient, scope, quantities, billing cadence and totals before sharing. The next step opens an editable email; nothing has been sent.</p>
  </article>;
}

function quoteEmail(quote: Quote, lead: Lead): [string, string] {
  const totals = quoteTotals(quote);
  const lines = quote.lines.map((line) => `• ${line.name} — ${line.quantity} × ${formatMoney(line.unitPrice)}${line.billing === "monthly" ? "/month" : ""}${line.discountPct ? ` (−${line.discountPct}%)` : ""}`).join("\n");
  const first = (lead.full_name || "").split(" ")[0] || "there";
  const body = [
    `Hi ${first},`,
    "",
    `Thank you for the conversation. Here is our quotation ${quote.number} for ${lead.company_name || "your team"}:`,
    "",
    lines,
    "",
    totals.monthly ? `One-time: ${formatMoney(totals.oneTime)} · Monthly: ${formatMoney(totals.monthly)} for ${quote.termMonths} months` : "",
    `Total contract value: ${formatMoney(totals.contractValue)}${quote.taxPct ? ` (incl. ${quote.taxPct}% tax)` : ""}`,
    `Valid until ${new Date(quote.validUntil).toLocaleDateString()}.`,
    quote.notes ? `\n${quote.notes}` : "",
    "",
    "Happy to walk through it on a call.",
  ].filter((line, index, all) => !(line === "" && all[index - 1] === "")).join("\n");
  return [`Quotation ${quote.number} — ${lead.company_name || lead.full_name}`, body];
}

function printQuote(quote: Quote, lead: Lead) {
  const totals = quoteTotals(quote);
  const escape = (value: string) => value.replace(/[&<>"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[char]!);
  const rows = quote.lines
    .map((line) => `<tr><td><b>${escape(line.name)}</b><br><small>${escape(line.description)}</small></td><td>${line.quantity}</td><td>${formatMoney(line.unitPrice)}${line.billing === "monthly" ? "/mo" : ""}</td><td>${line.discountPct ? `${line.discountPct}%` : "—"}</td><td>${formatMoney(lineTotal(line))}</td></tr>`)
    .join("");
  const html = `<!doctype html><html><head><meta charset="utf-8"><title>${escape(quote.number)}</title><style>
  body{font:14px/1.5 -apple-system,BlinkMacSystemFont,"Inter",sans-serif;color:#0c1116;margin:48px;max-width:780px}
  h1{font-size:28px;margin:0 0 4px;letter-spacing:-.02em}.muted{color:#5b6670}.top{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:36px}
  .mark{display:flex;gap:10px;align-items:center;font-weight:600}.mark i{width:28px;height:28px;border-radius:8px;background:#1e6b52;display:inline-block}
  table{width:100%;border-collapse:collapse;margin:24px 0}th{text-align:left;font-size:12px;color:#5b6670;font-weight:600;padding:8px 0;border-bottom:2px solid #0c1116}
  td{padding:12px 0;border-bottom:1px solid #e6e9e6;vertical-align:top}td:not(:first-child),th:not(:first-child){text-align:right}
  .totals{margin-left:auto;width:300px}.totals div{display:flex;justify-content:space-between;padding:4px 0}.grand{font-size:18px;font-weight:700;border-top:2px solid #0c1116;margin-top:6px;padding-top:10px!important}
  .notes{margin-top:32px;padding:16px;background:#f5f7f4;border-radius:10px;white-space:pre-wrap}</style></head><body>
  <div class="top"><div class="mark"><i></i>Ingenuity Systems</div><div style="text-align:right"><h1>Quotation</h1><div class="muted">${escape(quote.number)} · version ${quote.version}<br>Issued ${new Date(quote.createdAt).toLocaleDateString()} · valid until ${new Date(quote.validUntil).toLocaleDateString()}</div></div></div>
  <div><span class="muted">Prepared for</span><br><b>${escape(lead.company_name || "")}</b><br>${escape(lead.full_name || "")}${lead.email ? ` · ${escape(lead.email)}` : ""}</div>
  <table><thead><tr><th>Item</th><th>Qty</th><th>Price</th><th>Discount</th><th>Amount</th></tr></thead><tbody>${rows}</tbody></table>
  <div class="totals"><div><span>One-time</span><span>${formatMoney(totals.oneTime)}</span></div>${totals.monthly ? `<div><span>Monthly × ${quote.termMonths}</span><span>${formatMoney(totals.monthly)}/mo</span></div>` : ""}${totals.discount ? `<div><span>Discounts</span><span>−${formatMoney(totals.discount)}</span></div>` : ""}${quote.taxPct ? `<div><span>Tax (${quote.taxPct}%)</span><span>${formatMoney(totals.tax)}</span></div>` : ""}<div class="grand"><span>Total</span><span>${formatMoney(totals.contractValue)}</span></div></div>
  ${quote.notes ? `<div class="notes">${escape(quote.notes)}</div>` : ""}
  <script>window.onload=()=>setTimeout(()=>window.print(),200)</script></body></html>`;
  const win = window.open("", "_blank", "width=900,height=1000");
  if (!win) return;
  win.document.open();
  win.document.write(html);
  win.document.close();
}

/* ============================================================= Negotiation */

function NegotiationLog({ lead, canEdit, quoteValue }: { lead: Lead; canEdit: boolean; quoteValue: number | null }) {
  const store = useDeals();
  const entries = store.deals[lead.id]?.negotiation ?? [];
  const [party, setParty] = useState<"us" | "client">("client");
  const [kind, setKind] = useState<NegotiationKind>("counter");
  const [amount, setAmount] = useState("");
  const [summary, setSummary] = useState("");
  const priced = [...entries].reverse().filter((entry) => entry.amount != null);
  const max = Math.max(quoteValue ?? 0, ...priced.map((entry) => entry.amount ?? 0), 1);
  return (
    <section className="v-card v-card-pad d-section" aria-labelledby="d-neg">
      <header className="d-section-head">
        <span className="d-section-icon"><Icon name="handshake" size={17} /></span>
        <div>
          <h2 id="d-neg" className="v-card-title">Negotiation</h2>
          <p className="v-card-sub">Every offer, counter and concession in order, so nobody re-negotiates from memory.</p>
        </div>
      </header>
      {priced.length > 1 && (
        <div className="d-neg-chart" role="img" aria-label={`Price path: ${priced.map((entry) => `${entry.party === "us" ? "us" : "client"} ${formatMoney(entry.amount!)}`).join(", ")}`}>
          {quoteValue != null && <span className="d-neg-quote" style={{ bottom: `${(quoteValue / max) * 100}%` }}><small>Quote {formatMoney(quoteValue, "USD", true)}</small></span>}
          {priced.map((entry) => (
            <span key={entry.id} className="d-neg-point" data-party={entry.party} style={{ height: `${((entry.amount ?? 0) / max) * 100}%` }} title={`${entry.party === "us" ? "Us" : "Client"} · ${formatMoney(entry.amount!)}`} />
          ))}
        </div>
      )}
      {canEdit && (
        <form
          className="d-neg-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (!summary.trim()) return;
            dealActions.addNegotiation(lead.id, { party, kind, amount: amount === "" ? null : Math.max(0, Number(amount)), summary: summary.trim() });
            setSummary("");
            setAmount("");
          }}
        >
          <Segmented label="Who" size="sm" value={party} onChange={setParty} options={[{ value: "client", label: "Client" }, { value: "us", label: "Us" }]} />
          <select className="v-popup" aria-label="Kind" value={kind} onChange={(event) => setKind(event.target.value as NegotiationKind)}>
            {(Object.keys(NEGOTIATION_LABELS) as NegotiationKind[]).map((key) => <option key={key} value={key}>{NEGOTIATION_LABELS[key]}</option>)}
          </select>
          <input className="d-neg-amount" type="number" min="0" inputMode="decimal" aria-label="Amount in USD (optional)" placeholder="Amount" value={amount} onChange={(event) => setAmount(event.target.value)} />
          <input className="d-neg-summary" aria-label="What was said" placeholder="What was said or agreed" value={summary} onChange={(event) => setSummary(event.target.value)} />
          <Button kind="tinted" size="sm" type="submit" disabled={!summary.trim()}>Add</Button>
        </form>
      )}
      {entries.length ? (
        <ol className="d-neg-list">
          {entries.map((entry) => (
            <li key={entry.id} data-party={entry.party}>
              <span className="d-neg-who">{entry.party === "us" ? "Us" : "Client"}</span>
              <div>
                <b>{NEGOTIATION_LABELS[entry.kind]}{entry.amount != null ? <span className="num"> · {formatMoney(entry.amount)}</span> : null}</b>
                <p>{entry.summary}</p>
              </div>
              <time dateTime={entry.at}>{formatShortDate(entry.at)}</time>
              {canEdit && <IconButton icon="close" size={13} label="Remove entry" onClick={() => dealActions.removeNegotiation(lead.id, entry.id)} />}
            </li>
          ))}
        </ol>
      ) : (
        <p className="v-card-sub d-neg-empty">No rounds logged yet. Log the client’s first reaction to the quote here.</p>
      )}
    </section>
  );
}

/* ===================================================================== SLA */

function SlaCard({ lead, sla, canEdit }: { lead: Lead; sla: SlaTerms | null; canEdit: boolean }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<SlaTerms>(sla ?? defaultSla());
  useEffect(() => { if (!editing) setDraft(sla ?? defaultSla()); }, [sla, editing]);
  return (
    <section className="v-card v-card-pad d-sla" aria-labelledby="d-sla">
      <header className="d-section-head">
        <span className="d-section-icon"><Icon name="contract" size={17} /></span>
        <div>
          <h2 id="d-sla" className="v-card-title">Service levels</h2>
          <p className="v-card-sub">{sla ? `${SLA_STATUS_LABELS[sla.status]}${sla.signedAt ? ` · ${formatShortDate(sla.signedAt)}` : ""}` : "The final SLA you commit to."}</p>
        </div>
        {canEdit && !editing && <Button size="sm" onClick={() => setEditing(true)}>{sla ? "Edit" : "Draft SLA"}</Button>}
      </header>
      {editing ? (
        <form
          className="d-sla-form"
          onSubmit={(event) => {
            event.preventDefault();
            dealActions.saveSla(lead.id, { ...draft, signedAt: draft.status === "signed" ? draft.signedAt ?? new Date().toISOString() : null });
            setEditing(false);
          }}
        >
          <Segmented label="SLA status" size="sm" value={draft.status} onChange={(status) => setDraft({ ...draft, status })} options={(Object.keys(SLA_STATUS_LABELS) as SlaTerms["status"][]).map((key) => ({ value: key, label: SLA_STATUS_LABELS[key] }))} />
          <div className="d-sla-grid">
            <label className="v-field"><span>First response (hours)</span><input type="number" min="0" value={draft.responseHours} onChange={(event) => setDraft({ ...draft, responseHours: Number(event.target.value) })} /></label>
            <label className="v-field"><span>Resolution (hours)</span><input type="number" min="0" value={draft.resolutionHours} onChange={(event) => setDraft({ ...draft, resolutionHours: Number(event.target.value) })} /></label>
            <label className="v-field"><span>Uptime %</span><input type="number" min="0" max="100" step="0.1" value={draft.uptimePct} onChange={(event) => setDraft({ ...draft, uptimePct: Number(event.target.value) })} /></label>
            <label className="v-field"><span>Term (months)</span><input type="number" min="1" value={draft.termMonths} onChange={(event) => setDraft({ ...draft, termMonths: Number(event.target.value) })} /></label>
            <label className="v-field"><span>Support hours</span><input value={draft.supportWindow} onChange={(event) => setDraft({ ...draft, supportWindow: event.target.value })} /></label>
            <label className="v-field"><span>Start date</span><input type="date" value={draft.startDate} onChange={(event) => setDraft({ ...draft, startDate: event.target.value })} /></label>
            <label className="v-field d-span2"><span>Payment terms</span><input value={draft.paymentTerms} onChange={(event) => setDraft({ ...draft, paymentTerms: event.target.value })} /></label>
            <label className="v-field"><span>Renewal</span>
              <select value={draft.renewal} onChange={(event) => setDraft({ ...draft, renewal: event.target.value as SlaTerms["renewal"] })}>
                <option value="auto">Renews automatically</option>
                <option value="manual">Renew by agreement</option>
              </select>
            </label>
            {draft.status === "signed" && <label className="v-field"><span>Signed by</span><input value={draft.signedBy} placeholder="Name and title" onChange={(event) => setDraft({ ...draft, signedBy: event.target.value })} /></label>}
          </div>
          <div className="d-move-actions">
            <Button onClick={() => setEditing(false)}>Cancel</Button>
            <Button kind="filled" type="submit">Save Terms</Button>
          </div>
        </form>
      ) : sla ? (
        <dl className="d-sla-terms">
          <div><dt>First response</dt><dd>{sla.responseHours}h</dd></div>
          <div><dt>Resolution</dt><dd>{sla.resolutionHours}h</dd></div>
          <div><dt>Uptime</dt><dd>{sla.uptimePct}%</dd></div>
          <div><dt>Support</dt><dd>{sla.supportWindow}</dd></div>
          <div><dt>Term</dt><dd>{sla.termMonths} months · {sla.renewal === "auto" ? "auto-renews" : "renew by agreement"}</dd></div>
          <div><dt>Starts</dt><dd>{formatShortDate(sla.startDate)}</dd></div>
          <div><dt>Payment</dt><dd>{sla.paymentTerms}</dd></div>
          {sla.signedBy && <div><dt>Signed by</dt><dd>{sla.signedBy}</dd></div>}
        </dl>
      ) : (
        <p className="v-card-sub">Response and resolution times, uptime, support hours, term and payment — agreed before signature.</p>
      )}
      {sla?.status === "signed" && <Banner tone="tint" title="Signed — ready to record the win." />}
    </section>
  );
}
