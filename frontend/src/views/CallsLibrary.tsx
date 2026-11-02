import { useMemo, useState } from "react";
import { CallInsights, hasCallInsightData, type CallInsightsData } from "../components/CallInsights";
import { assessCallLeadPotential } from "../callLeadAssessment";
import { formatCallDuration } from "../callOutcome";
import { Icon } from "../v3/ui/Icon";
import { Badge, Button, EmptyState, Monogram } from "../v3/ui/primitives";

/** The slice of a call record the library tabs need. */
export type LibraryCall = {
  id: string;
  source: "stored" | "cloudtalk";
  provider: string;
  direction: string;
  customer_phone: string;
  started_at: string | null;
  created_at: string;
  duration_seconds: number | null;
  transcript: string | null;
  recording_url: string | null;
  insights: CallInsightsData | null;
  lead_id: string | null;
  lead_name: string | null;
  company_name: string | null;
};

const nameOf = (call: LibraryCall) => call.lead_name || call.company_name || call.customer_phone || "Unknown caller";
const when = (call: LibraryCall) => new Date(call.started_at || call.created_at);
const dateLabel = (call: LibraryCall) => when(call).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

/** Splits "Name: words" transcripts into speaker turns; plain text stays as one block. */
function turns(transcript: string): Array<{ speaker: string | null; text: string }> {
  const lines = transcript.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const parsed: Array<{ speaker: string | null; text: string }> = [];
  for (const line of lines) {
    const match = line.match(/^([A-Za-z][\w .'-]{0,32}):\s+(.*)$/);
    if (match) parsed.push({ speaker: match[1], text: match[2] });
    else if (parsed.length) parsed[parsed.length - 1].text += ` ${line}`;
    else parsed.push({ speaker: null, text: line });
  }
  return parsed;
}

/* ------------------------------------------------------------ Transcripts */

export function TranscriptsTab({
  calls,
  loadingId,
  onLoad,
  onOpenLead,
}: {
  calls: LibraryCall[];
  loadingId: string | null;
  onLoad: (call: LibraryCall) => void;
  onOpenLead: (leadId: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const needsLoad = useMemo(() => calls.filter((call) => call.source === "cloudtalk" && !call.transcript && !call.insights), [calls]);
  const shown = useMemo(() => {
    const term = query.trim().toLowerCase();
    return calls.filter((call) => !term || nameOf(call).toLowerCase().includes(term) || (call.transcript ?? "").toLowerCase().includes(term));
  }, [calls, query]);
  const selected = shown.find((call) => call.id === selectedId) ?? shown[0] ?? null;

  if (!calls.length) {
    return (
      <div className="v-card">
        <EmptyState icon="note" title="No calls to review yet">
          Refresh calls or connect a phone provider to load transcripts and analysis.
        </EmptyState>
      </div>
    );
  }
  return (
    <div className="cl-split">
      <aside className="cl-list" aria-label="Calls with transcripts">
        <label className="calls-search">
          <Icon name="search" size={15} />
          <span className="v-sr">Search transcripts</span>
          <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search names or what was said" />
        </label>
        <ul>
          {shown.map((call) => (
            <li key={call.id}>
              <button type="button" aria-current={selected?.id === call.id || undefined} onClick={() => setSelectedId(call.id)}>
                <Monogram text={nameOf(call)} size={32} />
                <span><b>{nameOf(call)}</b><small>{dateLabel(call)} · {formatCallDuration(call.duration_seconds)}</small></span>
              </button>
            </li>
          ))}
          {!shown.length && <li className="cl-none">Nothing matches “{query}”.</li>}
        </ul>
          {needsLoad.length > 0 && <p className="v-card-sub">Select any CloudTalk call to load its transcript and analysis.</p>}
      </aside>
      {selected && (
        <article className="cl-reader" aria-label={`Transcript with ${nameOf(selected)}`}>
          <header>
            <div>
              <h2>{nameOf(selected)}</h2>
              <p>{dateLabel(selected)} · {selected.direction.toLowerCase() === "inbound" ? "Inbound" : "Outbound"} · {formatCallDuration(selected.duration_seconds)} · {selected.customer_phone}</p>
            </div>
            <div className="cl-reader-actions">
              {selected.recording_url && <a className="v-btn" data-kind="gray" data-size="sm" href={selected.recording_url} target="_blank" rel="noopener noreferrer">Recording</a>}
              {selected.lead_id && <Button kind="gray" size="sm" onClick={() => onOpenLead(selected.lead_id!)}>Open lead</Button>}
            </div>
          </header>
          {selected.transcript?.trim() ? <ol className="cl-turns">
            {turns(selected.transcript).map((turn, index) => (
              <li key={index} data-side={turn.speaker && /agent|you|rep|corral/i.test(turn.speaker) ? "us" : "them"}>
                {turn.speaker && <b>{turn.speaker}</b>}
                <p>{turn.text}</p>
              </li>
            ))}
          </ol> : <EmptyState icon="note" title="Transcript not loaded yet" action={selected.source === "cloudtalk" ? <Button kind="filled" onClick={() => onLoad(selected)} disabled={loadingId === selected.id}>{loadingId === selected.id ? "Loading transcript…" : "Load transcript and analysis"}</Button> : undefined}>
            {selected.source === "cloudtalk" ? "Load this call to retrieve the provider transcript and available AI analysis." : "This provider did not supply a transcript for this call."}
          </EmptyState>}
        </article>
      )}
    </div>
  );
}

/* ------------------------------------------------------------ AI analysis */

export function AnalysisTab({ calls, loadingId, onLoad, onOpenLead }: { calls: LibraryCall[]; loadingId: string | null; onLoad: (call: LibraryCall) => void; onOpenLead: (leadId: string) => void }) {
  const analysed = useMemo(() => calls.filter((call) => hasCallInsightData(call.insights) || call.transcript?.trim()), [calls]);
  const stats = useMemo(() => {
    const sentiments = new Map<string, number>();
    const topics = new Map<string, number>();
    const actions: Array<{ text: string; call: LibraryCall }> = [];
    for (const call of analysed) {
      const sentiment = call.insights?.overall_sentiment?.trim();
      if (sentiment) sentiments.set(sentiment, (sentiments.get(sentiment) ?? 0) + 1);
      for (const topic of call.insights?.topics ?? []) if (topic.topic?.trim()) topics.set(topic.topic.trim(), (topics.get(topic.topic.trim()) ?? 0) + 1);
      for (const action of call.insights?.smart_notes?.action_items ?? []) if (action?.trim()) actions.push({ text: action.trim(), call });
    }
    return {
      sentiments: [...sentiments.entries()].sort((a, b) => b[1] - a[1]),
      topics: [...topics.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8),
      actions: actions.slice(0, 8),
    };
  }, [analysed]);

  if (!calls.length) {
    return (
      <div className="v-card">
        <EmptyState icon="sparkle" title="No calls to analyse yet">
          Refresh calls or connect a phone provider to load transcripts and provider analysis.
        </EmptyState>
      </div>
    );
  }
  return (
    <div className="cl-analysis">
      <section className="cl-overview" aria-label="Across your calls">
        <div className="v-card v-card-pad">
          <h2 className="v-card-title">Across {analysed.length} analysed call{analysed.length === 1 ? "" : "s"}</h2>
          {stats.sentiments.length ? (
            <ul className="cl-chips">{stats.sentiments.map(([name, count]) => <li key={name}><b>{count}</b> {name}</li>)}</ul>
          ) : <p className="v-card-sub">No sentiment recorded yet.</p>}
          {stats.topics.length > 0 && (
            <>
              <h3 className="cl-sub">Common topics</h3>
              <ul className="cl-chips cl-chips-quiet">{stats.topics.map(([name, count]) => <li key={name}>{name}{count > 1 ? <b> {count}</b> : null}</li>)}</ul>
            </>
          )}
        </div>
        <div className="v-card v-card-pad">
          <h2 className="v-card-title">Follow-ups promised</h2>
          {stats.actions.length ? (
            <ul className="cl-actions">{stats.actions.map((item, index) => (
              <li key={index}>
                <Icon name="check" size={14} />
                <span>{item.text}<small>{nameOf(item.call)} · {dateLabel(item.call)}</small></span>
              </li>
            ))}</ul>
          ) : <p className="v-card-sub">No action items were found in these calls.</p>}
        </div>
      </section>
      <ul className="cl-cards">
        {calls.map((call) => {
          const assessment = assessCallLeadPotential({ summary: call.insights?.summary, transcript: call.transcript });
          return (
            <li key={call.id} className="v-card v-card-pad cl-card">
              <header>
                <Monogram text={nameOf(call)} size={36} />
                <div>
                  <h3>{nameOf(call)}</h3>
                  <p>{dateLabel(call)} · {formatCallDuration(call.duration_seconds)}</p>
                </div>
                <Badge tone="neutral">{assessment.label}</Badge>
                {call.lead_id && <Button kind="plain" size="sm" onClick={() => onOpenLead(call.lead_id!)}>Open lead</Button>}
              </header>
              <p className="cl-assess">{assessment.detail}</p>
              {hasCallInsightData(call.insights) ? <CallInsights insights={call.insights} /> : call.source === "cloudtalk" ? <Button kind="gray" size="sm" onClick={() => onLoad(call)} disabled={loadingId === call.id}>{loadingId === call.id ? "Loading analysis…" : "Load transcript and analysis"}</Button> : <p className="v-card-sub">The provider did not supply structured insights for this call.</p>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/* --------------------------------------------------------------- Messages */

export function MessagesTab({ onOpenIntegrations, canViewIntegrations }: { onOpenIntegrations: () => void; canViewIntegrations: boolean }) {
  return (
    <div className="v-card cl-messages">
      <EmptyState icon="mail" title="Text messages are not connected yet" action={canViewIntegrations ? <Button kind="filled" onClick={onOpenIntegrations}>Open integrations</Button> : undefined}>
        SMS and MMS conversations will live here, next to your calls and linked to the same lead. Corral does not send or store text messages today, so nothing appears until a messaging provider is connected and supported.
      </EmptyState>
      <ul className="cl-plan" aria-label="What this tab will do">
        <li><b>One thread per lead.</b> Texts and picture messages sit beside emails and calls in the lead’s history.</li>
        <li><b>Consent first.</b> Sending respects the lead’s recorded consent for text messages.</li>
        <li><b>Review before it goes.</b> Replies Corral drafts wait for your approval, like email.</li>
      </ul>
    </div>
  );
}
