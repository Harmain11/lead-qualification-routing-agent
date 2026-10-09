import { useEffect, useState } from "react";
import { listLeads } from "../api";
import {
  opsGet,
  opsSave,
  type Insight,
  type PolicyResponse,
} from "../operationsApi";
import type { Lead } from "../types";
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
  const [kind, setKind] = useState("all");
  const [items, setItems] = useState<Insight[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [search, setSearch] = useState("");
  const [leadSearch, setLeadSearch] = useState("");
  const [leads, setLeads] = useState<Lead[]>([]);
  const [leadId, setLeadId] = useState("");
  const [members, setMembers] = useState<PolicyResponse["members"]>([]);
  const [memberId, setMemberId] = useState("");
  const [objections, setObjections] = useState<
    {
      tag: string;
      excerpt: string;
      outcome: string;
      lead_id: string;
      recorded_response?: { text: string; source: string } | null;
    }[]
  >([]);
  useEffect(() => {
    const controller = new AbortController();
    opsGet<{ items: Insight[] }>(
      `/v1/intelligence/insights?kind=${kind}`,
      controller.signal,
    )
      .then((data) => setItems(data.items))
      .catch((reason) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    return () => controller.abort();
  }, [kind, revision]);
  useEffect(() => {
    const controller = new AbortController();
    listLeads({ search: leadSearch, limit: 20 }, controller.signal)
      .then((data) => setLeads(data.items))
      .catch(() => {});
    return () => controller.abort();
  }, [leadSearch]);
  useEffect(() => {
    const controller = new AbortController();
    if (canReview)
      opsGet<PolicyResponse>("/v1/ops/policy", controller.signal)
        .then((data) => setMembers(data.members))
        .catch(() => {});
    return () => controller.abort();
  }, [canReview]);
  async function action(task: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await task();
      setRevision((value) => value + 1);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Unable to complete agent action",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="page-view ops-page">
      <header className="ops-page-head">
        <div>
          <span className="eyebrow">Evidence and judgment</span>
          <h1>Intelligence & coaching</h1>
          <p>
            Review source-backed research, conversation coaching, outcome
            learning and watcher alerts.
          </p>
        </div>
      </header>
      {error && (
        <p role="alert" className="team-alert">
          {error}
        </p>
      )}
      <section className="ops-panel">
        <div className="ops-form-grid">
          <label>
            Find a lead
            <input
              value={leadSearch}
              onChange={(event) => setLeadSearch(event.target.value)}
            />
          </label>
          <label>
            Lead
            <select
              value={leadId}
              onChange={(event) => setLeadId(event.target.value)}
            >
              <option value="">Choose a lead</option>
              {leads.map((lead) => (
                <option key={lead.id} value={lead.id}>
                  {lead.full_name} · {lead.company_name}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="ops-toolbar">
          {canExecute && (
            <button
              className="primary-button"
              disabled={!leadId || busy}
              onClick={() =>
                void action(() =>
                  opsSave(`/v1/intelligence/leads/${leadId}/coach`, {}),
                )
              }
            >
              Review recorded conversation
            </button>
          )}
          {canLearn && (
            <button
              className="secondary-button"
              disabled={busy}
              onClick={() =>
                void action(() => opsSave("/v1/intelligence/learning", {}))
              }
            >
              Learn from won/lost outcomes
            </button>
          )}
          <button
            className="button-quiet"
            onClick={() =>
              void action(async () =>
                setObjections(
                  (
                    await opsGet<{ items: typeof objections }>(
                      "/v1/intelligence/objections",
                    )
                  ).items,
                ),
              )
            }
          >
            Open objection library
          </button>
        </div>
        {canReview && (
          <div className="ops-toolbar">
            <label>
              Weekly coaching recipient
              <select
                value={memberId}
                onChange={(event) => setMemberId(event.target.value)}
              >
                <option value="">Choose a member</option>
                {members.map((member) => (
                  <option key={member.user_id} value={member.user_id}>
                    {member.name}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="secondary-button"
              disabled={!memberId || busy}
              onClick={() =>
                void action(() =>
                  opsSave("/v1/intelligence/digests", { user_id: memberId }),
                )
              }
            >
              Prepare manager-reviewed digest
            </button>
          </div>
        )}
      </section>
      <div className="ops-toolbar">
        <label>
          Report type
          <select
            value={kind}
            onChange={(event) => setKind(event.target.value)}
          >
            {["all", "research", "coach", "learning", "digest", "watcher"].map(
              (value) => (
                <option key={value}>{value}</option>
              ),
            )}
          </select>
        </label>
        <label>
          Search reports
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
        <button
          className="button-quiet"
          onClick={() => setRevision((value) => value + 1)}
        >
          Refresh
        </button>
      </div>
      {!items.length && (
        <section className="ops-empty">
          <h3>No reports yet</h3>
          <p>
            Review a lead conversation here, or approve a company's website in
            Accounts and request research. Enabled watchers will add alerts when
            new acquired evidence arrives.
          </p>
        </section>
      )}
      {items
        .filter((item) =>
          item.content.summary.toLowerCase().includes(search.toLowerCase()),
        )
        .map((item) => (
          <article className="ops-panel" key={item.id}>
            <div className="ops-toolbar">
              <span className="ops-pill">
                {item.kind} · {item.status.replaceAll("_", " ")}
              </span>
              <time>{new Date(item.created_at).toLocaleString()}</time>
              {item.lead_id && (
                <button
                  className="ops-link"
                  onClick={() => onInspect(item.lead_id!)}
                >
                  Open lead
                </button>
              )}
            </div>
            <h2>{item.content.summary}</h2>
            {item.content.scorecard && (
              <div className="ops-metrics">
                <span>
                  <b>{item.content.scorecard.recorded_outbound_emails}</b>
                  Recorded rep emails
                </span>
                <span>
                  <b>{item.content.scorecard.questions}</b>Questions asked
                </span>
                <span>
                  <b>{item.content.scorecard.questions_per_100_words ?? "—"}</b>
                  Questions / 100 words
                </span>
              </div>
            )}
            {item.content.recommendations?.length && (
              <ul>
                {item.content.recommendations.map((value, index) => (
                  <li key={index}>{value}</li>
                ))}
              </ul>
            )}
            {item.content.facts?.map((fact, index) => (
              <div className="ops-evidence" key={index}>
                <strong>
                  {fact.field}: {fact.value}
                </strong>
                <blockquote>{fact.excerpt}</blockquote>
                {/^https:\/\//.test(fact.source) ? (
                  <a href={fact.source} target="_blank" rel="noreferrer">
                    Source page
                  </a>
                ) : (
                  <small>Source: {fact.source}</small>
                )}
              </div>
            ))}
            {item.content.deltas?.length ? (
              <div className="ops-table-wrap">
                <table className="ops-table">
                  <thead>
                    <tr>
                      <th>Component</th>
                      <th>Criterion</th>
                      <th>Current</th>
                      <th>Proposed</th>
                    </tr>
                  </thead>
                  <tbody>
                    {item.content.deltas.map((row) => (
                      <tr key={row.component + row.criterion}>
                        <td>{row.component.replaceAll("_", " ")}</td>
                        <td>{row.criterion.replaceAll("_", " ")}</td>
                        <td>{row.from}</td>
                        <td>{row.to}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p>
                  Comparable sample: {item.content.wins} wins /{" "}
                  {item.content.losses} losses.
                </p>
              </div>
            ) : null}
            {item.content.suggestions?.map((suggestion, index) => (
              <div className="ops-note" key={index}>
                <p>{suggestion.recommendation}</p>
                {suggestion.evidence.map((evidence, i) => (
                  <blockquote key={i}>{evidence.text}</blockquote>
                ))}
                <button
                  className="ops-link"
                  onClick={() => onInspect(suggestion.lead_id)}
                >
                  Review supporting lead
                </button>
              </div>
            ))}
            {item.content.battlecards?.map((card) => (
              <div className="ops-note" key={card.competitor}>
                <h3>{card.competitor}</h3>
                <p>{card.response}</p>
                {card.evidence_url && /^https:\/\//.test(card.evidence_url) && (
                  <a href={card.evidence_url} target="_blank" rel="noreferrer">
                    Battlecard evidence
                  </a>
                )}
              </div>
            ))}
            {item.content.evidence?.length && (
              <details>
                <summary>Supporting conversation evidence</summary>
                {item.content.evidence.map((evidence, index) => (
                  <blockquote key={index}>
                    {evidence.text}
                    <small>
                      {evidence.direction} · {evidence.source}
                    </small>
                  </blockquote>
                ))}
              </details>
            )}
            {item.content.limitations && (
              <p className="ops-muted">{item.content.limitations}</p>
            )}
            {item.content.draft_id && (
              <button className="secondary-button" onClick={onIcp}>
                Review and publish ICP draft
              </button>
            )}
            {canReview && item.status === "pending_review" && (
              <div className="ops-toolbar">
                <button
                  className="primary-button"
                  disabled={
                    busy ||
                    (item.kind === "learning" &&
                      (!canLearn || !item.content.deltas?.length))
                  }
                  onClick={() =>
                    void action(() =>
                      opsSave(`/v1/intelligence/insights/${item.id}/review`, {
                        approve: true,
                      }),
                    )
                  }
                >
                  {item.kind === "learning"
                    ? "Create ICP draft from reviewed changes"
                    : item.kind === "research"
                      ? "Approve company enrichment"
                      : item.kind === "digest"
                        ? "Approve for representative"
                        : "Mark reviewed"}
                </button>
                <button
                  className="button-quiet"
                  disabled={busy}
                  onClick={() =>
                    void action(() =>
                      opsSave(`/v1/intelligence/insights/${item.id}/review`, {
                        approve: false,
                      }),
                    )
                  }
                >
                  Dismiss
                </button>
              </div>
            )}
          </article>
        ))}
      {objections.length > 0 && (
        <section className="ops-panel">
          <h2>Objection library</h2>
          <p>
            Mentions from recorded conversations, tagged with the lead's
            observed outcome.
          </p>
          {objections.map((item, index) => (
            <article className="ops-note" key={index}>
              <strong>
                {item.tag} · {item.outcome}
              </strong>
              <blockquote>{item.excerpt}</blockquote>
              {item.recorded_response && (
                <>
                  <small>
                    Recorded response · {item.recorded_response.source}
                  </small>
                  <blockquote>{item.recorded_response.text}</blockquote>
                </>
              )}
              <button
                className="ops-link"
                onClick={() => onInspect(item.lead_id)}
              >
                Open evidence
              </button>
            </article>
          ))}
        </section>
      )}
    </div>
  );
}
