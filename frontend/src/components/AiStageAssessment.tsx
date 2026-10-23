import type { LeadAnalysis } from "../types";

const label = (value: string) => value.replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase());

export function AiStageAssessment({ analysis }: { analysis: LeadAnalysis | null }) {
  const available = analysis?.status === "completed" || analysis?.status === "manual_review";
  const stage = available ? analysis?.context.buying_stage : null;
  const value = stage?.value?.trim();
  const hasStage = Boolean(value && value.toLowerCase() !== "unknown" && stage?.information_kind !== "unknown");
  const needsReview = available && Boolean(analysis?.requires_manual_review);
  const assessedAt = analysis?.created_at ? new Date(analysis.created_at) : null;

  return <section className="ai-stage-assessment" aria-label="AI stage assessment">
    <h4>AI-assessed buying stage</h4>
    <strong className="ai-stage-value">{!available ? "AI assessment unavailable" : hasStage ? label(value!) : "Not enough evidence"}</strong>
    {hasStage && stage && <div className="ai-stage-certainty">
      <span>{stage.information_kind === "explicit" ? "Explicitly stated" : "AI estimate"}</span>
      <span>Stage confidence: {Math.round(Math.max(0, Math.min(1, stage.confidence)) * 100)}%</span>
    </div>}
    <p className="ai-stage-disclaimer">{hasStage
      ? "This describes the buyer’s likely stage, not a confirmed win or loss. Your recorded outcome stays separate."
      : available
        ? "The latest analysis cannot identify a buying stage. Use verified information when recording a manual stage."
        : "No usable AI stage assessment is available. Manual progress and closing outcomes use verified information."}</p>
    {needsReview && <p className="ai-stage-review">Human review required before relying on this assessment.</p>}
    {hasStage && analysis && stage && <details className="ai-stage-reasoning">
      <summary>Why AI thinks this</summary>
      {stage.evidence.length ? <ul>{stage.evidence.slice(0, 3).map((evidence, index) => <li key={`${evidence.source_field}-${index}`}><span>{label(evidence.source_field)}</span><q>{evidence.excerpt}</q></li>)}</ul>
        : <p>{analysis.summary.buying_signal?.trim() || "No stage-specific evidence was recorded."}</p>}
      {analysis.recommended_action?.trim() && <p><b>AI suggests:</b> {analysis.recommended_action}</p>}
    </details>}
    {assessedAt && !Number.isNaN(assessedAt.getTime()) && <small className="ai-stage-updated">Latest analysis: <time dateTime={analysis!.created_at}>{assessedAt.toLocaleString()}</time></small>}
  </section>;
}
