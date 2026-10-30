export type CallOutcome = "answered" | "missed" | "no_answer" | "failed" | "in_progress";

const IN_PROGRESS = new Set(["queued", "initiated", "initial", "ringing", "in_progress", "submitting", "pending", "started", "dialing"]);
const FAILED = new Set(["failed", "error", "canceled", "cancelled", "rejected", "blocked"]);
const UNANSWERED = new Set(["no_answer", "noanswer", "busy", "missed", "unanswered", "voicemail", "abandoned", "not_answered"]);
const FINISHED = new Set(["completed", "complete", "done", "ended", "answered", "finished", "success", "successful"]);

/** Same rules as the backend's call_outcome, for provider records that aren't stored yet. */
export function callOutcome(status: string | null | undefined, direction: string | null | undefined, durationSeconds: number | null | undefined): CallOutcome {
  const normalized = (status ?? "").trim().toLowerCase().replace(/[-\s]/g, "_");
  const inbound = ["inbound", "incoming", "in"].includes((direction ?? "").trim().toLowerCase());
  if (IN_PROGRESS.has(normalized)) return "in_progress";
  if (FAILED.has(normalized)) return "failed";
  if (UNANSWERED.has(normalized) || (FINISHED.has(normalized) && !durationSeconds)) return inbound ? "missed" : "no_answer";
  if (FINISHED.has(normalized) || durationSeconds) return "answered";
  return inbound ? "missed" : "no_answer";
}

export function formatCallDuration(seconds: number | null | undefined): string {
  if (!seconds) return "—";
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return minutes ? `${minutes}m ${String(rest).padStart(2, "0")}s` : `${rest}s`;
}
