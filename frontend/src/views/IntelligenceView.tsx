import { useEffect, useMemo, useState } from "react";
import {
  opsGet,
  opsSave,
  type Insight,
  type PolicyResponse,
} from "../operationsApi";
import type { Lead } from "../types";
import { LeadPicker } from "../components/LeadPicker";
import { Icon, type IconName } from "../v3/ui/Icon";
import {
  Badge,
  Banner,
  Button,
  EmptyState,
  PageHeader,
  Segmented,
  Sheet,
  type Tone,
} from "../v3/ui/primitives";
import { SelectMenu } from "../components/SelectMenu";

type Objection = {
  tag: string;
  excerpt: string;
  outcome: string;
  lead_id: string;
  recorded_response?: { text: string; source: string } | null;
};

type ReportKind = "all" | "coach" | "research" | "learning" | "digest" | "watcher";

const REPORT_KINDS: Array<{ value: ReportKind; label: string }> = [
  { value: "all", label: "All" },
  { value: "coach", label: "Coaching" },
  { value: "research", label: "Research" },
  { value: "learning", label: "Win/loss" },
  { value: "digest", label: "Digests" },
  { value: "watcher", label: "Alerts" },
];

const KIND_META: Record<string, { label: string; icon: IconName }> = {
  coach: { label: "Conversation coaching", icon: "research" },
  research: { label: "Company research", icon: "companies" },
  learning: { label: "Win/loss learning", icon: "analytics" },
  digest: { label: "Weekly coaching digest", icon: "team" },
  watcher: { label: "Alert", icon: "alert" },
};

const STATUS_META: Record<string, { label: string; tone: Tone }> = {
  pending_review: { label: "Needs review", tone: "attention" },
  approved: { label: "Approved", tone: "tint" },
  reviewed: { label: "Reviewed", tone: "tint" },
  dismissed: { label: "Dismissed", tone: "neutral" },
  rejected: { label: "Dismissed", tone: "neutral" },
};

function humanize(value: string) {
  const text = value.replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function when(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? ""
    : date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

export function IntelligenceView({
  onInspect,
  onIcp,
  canExecute,
  canReview,
  canLearn,
}: {
  onInspect: (id: string) => void;
  onIcp: () => void;
  canExecute: boolean;
  canReview: boolean;
  canLearn: boolean;
}) {
  const [kind, setKind] = useState<ReportKind>("all");
  const [items, setItems] = useState<Insight[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [search, setSearch] = useState("");
  const [lead, setLead] = useState<Lead | null>(null);
  const [pickingLead, setPickingLead] = useState(false);
  const [members, setMembers] = useState<PolicyResponse["members"]>([]);
  const [memberId, setMemberId] = useState("");
  const [objections, setObjections] = useState<Objection[] | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    opsGet<{ items: Insight[] }>(`/v1/intelligence/insights?kind=${kind}`, controller.signal)
      .then((data) => { setItems(data.items); setError(""); })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Reports couldn't be loaded.");
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [kind, revision]);

  useEffect(() => {
    if (!canReview) return;
    const controller = new AbortController();
    opsGet<PolicyResponse>("/v1/ops/policy", controller.signal)
      .then((data) => setMembers(data.members))
      .catch(() => {});
    return () => controller.abort();
  }, [canReview]);

  async function run(task: () => Promise<unknown>, success: string) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await task();
      setNotice(success);
      setRevision((value) => value + 1);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "That didn't work. Try again.");
    } finally {
      setBusy(false);
    }
  }

  async function openObjections() {
    setBusy(true);
    setError("");
    try {
      setObjections((await opsGet<{ items: Objection[] }>("/v1/intelligence/objections")).items);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The objection library couldn't be loaded.");
    } finally {
      setBusy(false);
    }
  }

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return needle ? items.filter((item) => item.content.summary.toLowerCase().includes(needle)) : items;
  }, [items, search]);
  const waiting = items.filter((item) => item.status === "pending_review").length;

  return (
    <div className="page-view coach-page">
      <PageHeader
        title="Research & Coaching"
        subtitle="Coaching from your recorded calls and emails, research on accounts, and what your wins and losses teach. Every finding links to its evidence."
        actions={<Button kind="gray" icon="listBullets" disabled={busy} onClick={() => void openObjections()}>Objection library</Button>}
      />
      {error && <Banner tone="danger">{error}</Banner>}
      {notice && <Banner tone="tint">{notice}</Banner>}

      <div className="coach-actions">
        {canExecute && (
          <section className="coach-tile">
            <span className="coach-tile-icon"><Icon name="research" size={18} /></span>
            <h2>Coach a conversation</h2>
            <p>Reviews a lead’s recorded calls and emails: questions asked, talk balance, and what to do next.</p>
            <button type="button" className="coach-lead" onClick={() => setPickingLead(true)}>
              {lead ? (
                <span><b>{lead.full_name || lead.company_name}</b><small>{lead.company_name}</small></span>
              ) : (
                <span className="coach-lead-empty">Choose a lead</span>
              )}
              <Icon name="chevronRight" size={14} />
            </button>
            <Button
              kind="filled"
              disabled={!lead || busy}
              onClick={() => lead && void run(
                () => opsSave(`/v1/intelligence/leads/${lead.id}/coach`, {}),
                `Coaching report for ${lead.full_name || lead.company_name} is ready below.`,
              )}
            >
              Review conversation
            </Button>
          </section>
        )}
        {canReview && (
          <section className="coach-tile">
            <span className="coach-tile-icon"><Icon name="team" size={18} /></span>
            <h2>Weekly coaching digest</h2>
            <p>Prepares a rep’s week in review for you to check before they see it.</p>
            <label className="v-field">
              <span>Rep</span>
              <SelectMenu
                ariaLabel="Rep"
                value={memberId}
                onChange={setMemberId}
                options={[{ value: "", label: "Choose a rep" }, ...members.map((member) => ({ value: member.user_id, label: member.name }))]}
              />
            </label>
            <Button
              kind="gray"
              disabled={!memberId || busy}
              onClick={() => void run(
                () => opsSave("/v1/intelligence/digests", { user_id: memberId }),
                "Digest prepared. Review it below before it goes to the rep.",
              )}
            >
              Prepare digest
            </Button>
          </section>
        )}
        {canLearn && (
          <section className="coach-tile">
            <span className="coach-tile-icon"><Icon name="analytics" size={18} /></span>
            <h2>Learn from wins and losses</h2>
            <p>Compares closed deals and suggests changes to your ideal customer profile. Nothing changes until you approve.</p>
            <Button
              kind="gray"
              disabled={busy}
              onClick={() => void run(
                () => opsSave("/v1/intelligence/learning", {}),
                "Win/loss analysis finished. Suggested changes are below.",
              )}
            >
              Analyze outcomes
            </Button>
          </section>
        )}
      </div>

      <section className="coach-reports" aria-labelledby="coach-reports-title">
        <div className="coach-reports-head">
          <h2 id="coach-reports-title">
            Reports {waiting > 0 && <Badge tone="attention">{waiting} to review</Badge>}
          </h2>
          <div className="coach-filters">
            <div className="coach-seg"><Segmented label="Report type" size="sm" value={kind} options={REPORT_KINDS} onChange={setKind} /></div>
            <label className="calls-search">
              <Icon name="search" size={15} />
              <span className="v-sr">Search reports</span>
              <input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search reports" />
            </label>
          </div>
        </div>
        {loading && !items.length ? (
          <p className="mailbox-empty" role="status">Loading reports…</p>
        ) : !visible.length ? (
          <EmptyState icon="research" title={search ? "No reports match your search" : "No reports yet"}>
            {search
              ? "Try a different word or clear the search."
              : "Coach a conversation above, or request research from a company in Companies. Reports appear here with their evidence."}
          </EmptyState>
        ) : (
          <div className="coach-list">
            {visible.map((item) => (
              <ReportCard
                key={item.id}
                item={item}
                busy={busy}
                canReview={canReview}
                canLearn={canLearn}
                onInspect={onInspect}
                onIcp={onIcp}
                onReview={(approve) => void run(
                  () => opsSave(`/v1/intelligence/insights/${item.id}/review`, { approve }),
                  approve ? "Report approved." : "Report dismissed.",
                )}
              />
            ))}
          </div>
        )}
      </section>

      {pickingLead && (
        <Sheet title="Choose a lead to coach" subtitle="Corral reviews its recorded calls and emails." onClose={() => setPickingLead(false)} width={520}>
          <LeadPicker
            placeholder="Search leads"
            onPick={(picked) => { setLead(picked); setPickingLead(false); }}
          />
        </Sheet>
      )}

      {objections && (
        <Sheet title="Objection library" subtitle="Objections raised in recorded conversations, with how each deal ended." onClose={() => setObjections(null)} width={640}>
          {!objections.length ? (
            <EmptyState icon="listBullets" title="No objections recorded yet">
              They appear here once recorded calls or emails mention price, timing, competitors or authority.
            </EmptyState>
          ) : (
            <ul className="coach-objections">
              {objections.map((item, index) => (
                <li key={index}>
                  <div className="coach-objection-head">
                    <Badge tone="info">{humanize(item.tag)}</Badge>
                    <Badge tone={/won|convert/i.test(item.outcome) ? "tint" : /lost/i.test(item.outcome) ? "danger" : "neutral"}>{humanize(item.outcome)}</Badge>
                  </div>
                  <blockquote>{item.excerpt}</blockquote>
                  {item.recorded_response && (
                    <div className="coach-response">
                      <small>How the rep answered · {item.recorded_response.source}</small>
                      <p>{item.recorded_response.text}</p>
                    </div>
                  )}
                  <Button size="sm" kind="plain" trailingIcon="chevronRight" onClick={() => { setObjections(null); onInspect(item.lead_id); }}>
                    Open lead
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </Sheet>
      )}
    </div>
  );
}

function ReportCard({
  item,
  busy,
  canReview,
  canLearn,
  onInspect,
  onIcp,
  onReview,
}: {
  item: Insight;
  busy: boolean;
  canReview: boolean;
  canLearn: boolean;
  onInspect: (id: string) => void;
  onIcp: () => void;
  onReview: (approve: boolean) => void;
}) {
  const meta = KIND_META[item.kind] ?? { label: humanize(item.kind), icon: "info" as IconName };
  const status = STATUS_META[item.status] ?? { label: humanize(item.status), tone: "neutral" as Tone };
  const content = item.content;
  const approveLabel = item.kind === "learning"
    ? "Create ICP draft"
    : item.kind === "research"
      ? "Approve research"
      : item.kind === "digest"
        ? "Send to rep"
        : "Mark reviewed";
  return (
    <article className="coach-card">
      <header className="coach-card-head">
        <span className="coach-card-kind"><Icon name={meta.icon} size={14} />{meta.label}</span>
        <Badge tone={status.tone} dot>{status.label}</Badge>
        <time dateTime={item.created_at}>{when(item.created_at)}</time>
        {item.lead_id && (
          <Button size="sm" kind="plain" trailingIcon="chevronRight" onClick={() => onInspect(item.lead_id!)}>Open lead</Button>
        )}
      </header>
      <h3>{content.summary}</h3>

      {content.scorecard && (
        <dl className="coach-metrics">
          <div><dt>Rep emails reviewed</dt><dd>{content.scorecard.recorded_outbound_emails}</dd></div>
          <div><dt>Questions asked</dt><dd>{content.scorecard.questions}</dd></div>
          <div><dt>Questions per 100 words</dt><dd>{content.scorecard.questions_per_100_words ?? "—"}</dd></div>
        </dl>
      )}

      {!!content.recommendations?.length && (
        <div className="coach-section">
          <h4>What to do next</h4>
          <ol>{content.recommendations.map((value, index) => <li key={index}>{value}</li>)}</ol>
        </div>
      )}

      {!!content.suggestions?.length && (
        <div className="coach-section">
          <h4>Coaching moments</h4>
          {content.suggestions.map((suggestion, index) => (
            <div className="coach-note" key={index}>
              <p>{suggestion.recommendation}</p>
              {suggestion.evidence.map((evidence, i) => <blockquote key={i}>{evidence.text}</blockquote>)}
              <Button size="sm" kind="plain" trailingIcon="chevronRight" onClick={() => onInspect(suggestion.lead_id)}>See the conversation</Button>
            </div>
          ))}
        </div>
      )}

      {!!content.facts?.length && (
        <div className="coach-section">
          <h4>Findings</h4>
          {content.facts.map((fact, index) => (
            <div className="coach-note" key={index}>
              <p><b>{humanize(fact.field)}:</b> {fact.value}</p>
              <blockquote>{fact.excerpt}</blockquote>
              {/^https:\/\//.test(fact.source)
                ? <a className="coach-source" href={fact.source} target="_blank" rel="noreferrer">Source page</a>
                : <small className="coach-source">Source: {fact.source}</small>}
            </div>
          ))}
        </div>
      )}

      {!!content.deltas?.length && (
        <div className="coach-section">
          <h4>Suggested profile changes</h4>
          <div className="coach-table" role="region" aria-label="Suggested profile changes" tabIndex={0}>
            <table>
              <thead><tr><th>Area</th><th>Criterion</th><th>Now</th><th>Suggested</th></tr></thead>
              <tbody>
                {content.deltas.map((row) => (
                  <tr key={row.component + row.criterion}>
                    <td>{humanize(row.component)}</td>
                    <td>{humanize(row.criterion)}</td>
                    <td className="num">{row.from}</td>
                    <td className="num">{row.to}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="coach-muted">Based on {content.wins ?? 0} won and {content.losses ?? 0} lost deals.</p>
        </div>
      )}

      {!!content.battlecards?.length && (
        <div className="coach-section">
          <h4>Competitor battlecards</h4>
          {content.battlecards.map((card) => (
            <div className="coach-note" key={card.competitor}>
              <p><b>{card.competitor}</b></p>
              <p>{card.response}</p>
              {card.evidence_url && /^https:\/\//.test(card.evidence_url) && (
                <a className="coach-source" href={card.evidence_url} target="_blank" rel="noreferrer">Evidence</a>
              )}
            </div>
          ))}
        </div>
      )}

      {!!content.evidence?.length && (
        <details className="coach-evidence">
          <summary>Conversation evidence ({content.evidence.length})</summary>
          {content.evidence.map((evidence, index) => (
            <blockquote key={index}>
              {evidence.text}
              <small>{humanize(evidence.direction)} · {evidence.source}</small>
            </blockquote>
          ))}
        </details>
      )}

      {content.limitations && <p className="coach-muted">{content.limitations}</p>}

      {(content.draft_id || (canReview && item.status === "pending_review")) && (
        <footer className="coach-card-actions">
          {content.draft_id && <Button kind="tinted" onClick={onIcp}>Review ICP draft</Button>}
          {canReview && item.status === "pending_review" && (
            <>
              <Button
                kind="filled"
                disabled={busy || (item.kind === "learning" && (!canLearn || !content.deltas?.length))}
                onClick={() => onReview(true)}
              >
                {approveLabel}
              </Button>
              <Button kind="gray" disabled={busy} onClick={() => onReview(false)}>Dismiss</Button>
            </>
          )}
        </footer>
      )}
    </article>
  );
}
