import { assessCallLeadPotential } from "../callLeadAssessment";
import { CallInsights, hasCallInsightData, type CallInsightsData } from "./CallInsights";

export function CallReviewDetails({
  transcript,
  recordingUrl,
  message,
  insights,
}: {
  transcript: string | null;
  recordingUrl: string | null;
  message: string | null;
  insights: CallInsightsData;
}) {
  const hasTranscript = Boolean(transcript?.trim());
  const hasInsights = hasCallInsightData(insights);
  const assessment = assessCallLeadPotential({
    summary: insights?.summary,
    transcript,
  });

  return (
    <div className="call-review-details">
      {!hasTranscript && !hasInsights && (
        <p className="call-detail-note">No transcript or AI findings are available for this call.</p>
      )}
      {!hasTranscript && hasInsights && (
        <p className="call-detail-note">No transcript is available; showing provider-generated insights.</p>
      )}
      {hasInsights && <CallInsights insights={insights} />}
      {recordingUrl && (
        <a className="call-review-recording" href={recordingUrl} target="_blank" rel="noopener noreferrer">
          Open provider recording or call details
          <span aria-hidden="true">↗</span>
        </a>
      )}
      {hasTranscript && (
        <details className="call-review-transcript">
          <summary>
            <span>View transcript</span>
            <small>Full conversation</small>
          </summary>
          <pre className="call-transcript-text">{transcript}</pre>
        </details>
      )}
      {(hasTranscript || hasInsights) && (
        <div className="call-lead-assessment">
          <span className="call-lead-assessment-label">Call assessment</span>
          <strong>{assessment.label}</strong>
          <p>{assessment.detail}</p>
        </div>
      )}
      {message && <p className="call-record-receipt">{message}</p>}
    </div>
  );
}
