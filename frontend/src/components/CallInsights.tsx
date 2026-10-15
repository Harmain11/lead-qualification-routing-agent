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

function InsightList({ title, items }: { title: string; items: string[] }) {
  return (
    <section className="call-insight-group">
      <h5>{title}</h5>
      {items.length ? <ul>{items.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul> : <p className="call-insight-unavailable">{title} unavailable</p>}
    </section>
  );
}

export function CallInsights({ insights }: { insights: CallInsightsData }) {
  const notes = insights?.smart_notes;
  const topics = insights?.topics ?? [];
  const talkListen = insights?.talk_listen_ratio ?? [];

  return (
    <section className="call-insights" aria-label="Conversation intelligence">
      <h4>Conversation intelligence</h4>
      <div className="call-insight-summary">
        <span className="call-insight-label">Call summary</span>
        {insights?.summary?.trim()
          ? <p>{insights.summary}</p>
          : <p className="call-insight-unavailable">Call summary unavailable</p>}
      </div>

      <div className="call-insight-facts">
        <div>
          <span className="call-insight-label">Overall sentiment</span>
          <strong>{insights?.overall_sentiment?.trim() || "Unavailable"}</strong>
        </div>
        <div className="call-insight-topics">
          <span className="call-insight-label">Topics</span>
          {topics.length
            ? <ul>{topics.map((item, index) => <li key={`${item.topic}-${index}`}><span>{item.topic}</span>{item.weight !== null ? <small>Weight {item.weight}</small> : <small>Weight unavailable</small>}</li>)}</ul>
            : <p className="call-insight-unavailable">Topics unavailable</p>}
        </div>
      </div>

      <div className="call-insight-notes">
        <InsightList title="Action items" items={notes?.action_items ?? []} />
        <InsightList title="Decisions made" items={notes?.decisions_made ?? []} />
        <InsightList title="Open questions" items={notes?.open_questions ?? []} />
        <section className="call-insight-group">
          <h5>Important dates & deadlines</h5>
          {notes?.important_dates_and_deadlines?.length
            ? <ul>{notes.important_dates_and_deadlines.map((item, index) => <li key={`${item.event_description}-${index}`}><strong>{item.date_mention?.trim() || "Date mention unavailable"}</strong><span>{item.event_description}</span></li>)}</ul>
            : <p className="call-insight-unavailable">Important dates & deadlines unavailable</p>}
        </section>
        <InsightList title="Risks & issues" items={notes?.risks_and_issues ?? []} />
        <InsightList title="Customer feedback" items={notes?.customer_feedback ?? []} />
      </div>

      <section className="call-insight-group call-insight-ratio">
        <h5>Talk / listen ratio</h5>
        {talkListen.length
          ? <ul>{talkListen.map((item, index) => <li key={`${item.caller}-${index}`}><strong>{item.caller}</strong><span>Talking time: {item.talking_time !== null ? `${item.talking_time} s` : "Unavailable"}</span><span>Ratio: {item.ratio !== null ? `${item.ratio}%` : "Unavailable"}</span></li>)}</ul>
          : <p className="call-insight-unavailable">Talk / listen ratio unavailable</p>}
      </section>
    </section>
  );
}
