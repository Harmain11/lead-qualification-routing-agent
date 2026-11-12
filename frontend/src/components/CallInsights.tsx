type SmartNotes = {
  action_items: string[];
  decisions_made: string[];
  open_questions: string[];
  important_dates_and_deadlines: Array<{ date_mention: string | null; event_description: string }>;
  risks_and_issues: string[];
  customer_feedback: string[];
};

export type CallInsightsData = {
  summary: string | null;
  overall_sentiment: string | null;
  topics: Array<{ topic: string; weight: number | null }>;
  smart_notes: SmartNotes;
  talk_listen_ratio: Array<{ caller: string; talking_time: number | null; ratio: number | null }>;
} | null;

const nonEmpty = (value: string | null | undefined): value is string => Boolean(value?.trim());

export function hasCallInsightData(insights: CallInsightsData) {
  const notes = insights?.smart_notes;
  return Boolean(
    nonEmpty(insights?.summary) ||
    nonEmpty(insights?.overall_sentiment) ||
    insights?.topics.some((item) => nonEmpty(item.topic)) ||
    notes?.action_items.some(nonEmpty) ||
    notes?.decisions_made.some(nonEmpty) ||
    notes?.open_questions.some(nonEmpty) ||
    notes?.important_dates_and_deadlines.some((item) => nonEmpty(item.event_description)) ||
    notes?.risks_and_issues.some(nonEmpty) ||
    notes?.customer_feedback.some(nonEmpty)
  );
}

function InsightList({ title, items }: { title: string; items: string[] }) {
  const available = items.filter(nonEmpty);
  if (!available.length) return null;
  return <section className="call-insight-group"><h5>{title}</h5><ul>{available.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></section>;
}

function CoachingNextStep({ insights, notes }: { insights: CallInsightsData; notes: SmartNotes | undefined }) {
  const action = notes?.action_items.find(nonEmpty);
  const question = notes?.open_questions.find(nonEmpty);
  const risk = notes?.risks_and_issues.find(nonEmpty);
  const decision = notes?.decisions_made.find(nonEmpty);
  const detail = action || question || risk || decision;
  if (!detail && !nonEmpty(insights?.summary)) return null;

  const label = action ? "Follow through on the action item" : question ? "Resolve the open question" : risk ? "Address the call risk" : decision ? "Confirm the decision" : "Use the call summary for the next touch";
  return <section className="call-coaching" aria-label="Coaching and next step">
    <span className="call-insight-label">Coaching &amp; next step</span>
    <strong>{label}</strong>
    <p>{detail || insights?.summary}</p>
  </section>;
}

export function CallInsights({ insights }: { insights: CallInsightsData }) {
  const notes = insights?.smart_notes;
  const topics = (insights?.topics ?? []).filter((item) => nonEmpty(item.topic));
  const dates = (notes?.important_dates_and_deadlines ?? []).filter((item) => nonEmpty(item.event_description));
  if (!hasCallInsightData(insights)) return null;

  return (
    <section className="call-insights" aria-label="AI insights">
      {nonEmpty(insights?.summary) && <div className="call-insight-summary"><span className="call-insight-label">Summary</span><p>{insights.summary}</p></div>}
      {nonEmpty(insights?.overall_sentiment) && <span className="call-insight-sentiment">{insights.overall_sentiment}</span>}
      {topics.length > 0 && <section className="call-insight-group call-insight-topics"><h5>Topics</h5><ul>{topics.map((item, index) => <li key={`${item.topic}-${index}`}>{item.topic}</li>)}</ul></section>}
      <CoachingNextStep insights={insights} notes={notes} />
      <div className="call-insight-notes">
        <InsightList title="Action items" items={notes?.action_items ?? []} />
        <InsightList title="Decisions" items={notes?.decisions_made ?? []} />
        <InsightList title="Open questions" items={notes?.open_questions ?? []} />
        {dates.length > 0 && <section className="call-insight-group"><h5>Important dates</h5><ul>{dates.map((item, index) => <li key={`${item.event_description}-${index}`}>{nonEmpty(item.date_mention) && <strong>{item.date_mention}</strong>}<span>{item.event_description}</span></li>)}</ul></section>}
        <InsightList title="Risks" items={notes?.risks_and_issues ?? []} />
        <InsightList title="Customer feedback" items={notes?.customer_feedback ?? []} />
      </div>
    </section>
  );
}
