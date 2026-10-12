import type { ManualReviewAnalytics } from "../types";
import {
  Empty,
  pretty,
  QualificationBadge,
  Unavailable,
} from "./productShared";

export function ManualReviewView({
  data,
  onInspect,
}: {
  data: ManualReviewAnalytics | null;
  onInspect: (id: string) => void;
}) {
  if (!data) return <Unavailable />;
  return (
    <div className="page-view operational-page">
      <header className="page-hero compact-hero">
        <div>
          <span className="eyebrow">Human judgment queue</span>
          <h1>Manual review</h1>
          <p>
            Provider failures, low confidence and contradictory evidence pause
            here. The system does not invent a resolution.
          </p>
        </div>
        <div className="review-total">
          <strong>{data.total}</strong>
          <span>open review{data.total === 1 ? "" : "s"}</span>
        </div>
      </header>
      <section className="review-grid" data-tour="review">
        {data.items.length ? (
          data.items.map((lead) => (
            <article className="review-card panel" key={lead.id}>
              <div className="review-card-head">
                <span className={`source-tag source-${lead.source}`}>
                  {pretty(lead.source)}
                </span>
                <div className="review-qualification">
                  <b>{lead.score}</b>
                  <QualificationBadge value={lead.band} manualReview />
                </div>
              </div>
              <h2>{lead.company}</h2>
              <p>
                {lead.name} · {lead.service}
              </p>
              <div className="review-reasons">
                {lead.review_reasons.map((reason) => (
                  <span key={reason}>{reason}</span>
                ))}
              </div>
              <div>
                <small>Current queue</small>
                <b>{pretty(lead.routing_status)}</b>
              </div>
              <button onClick={() => onInspect(lead.id)}>
                Inspect evidence →
              </button>
            </article>
          ))
        ) : (
          <Empty
            title="Manual review is clear"
            text="No leads need human judgment in this period."
          />
        )}
      </section>
    </div>
  );
}
