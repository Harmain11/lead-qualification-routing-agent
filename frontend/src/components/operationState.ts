/**
 * The one vocabulary for the state of an operation Corral proposes. A proposal
 * only reads as done when the real request succeeded in this session; drafts
 * stay "not sent", the Dialer stays "no call placed".
 */
export type OperationState = { key: OperationKey; label: string; tone?: "attn" | "accent" | "danger" | "info" };
export type OperationKey = "suggested" | "approval" | "running" | "done" | "prepared" | "failed" | "cancelled";

const APPROVAL_KINDS = new Set(["update_profile", "add_note", "record_progress", "sync_crm", "delete_lead", "reevaluate_lead", "refresh_email"]);

export function operationState(
  kind: string,
  message: { busy?: boolean; done?: boolean; error?: string; cancelled?: boolean },
): OperationState {
  if (message.busy) return { key: "running", label: "Running", tone: "info" };
  if (message.error) return { key: "failed", label: "Failed", tone: "danger" };
  if (message.done) {
    if (kind === "draft_email" || kind === "send_follow_up") return { key: "prepared", label: "Draft open · not sent", tone: "attn" };
    if (kind === "open_dialer") return { key: "done", label: "Dialer opened · no call placed", tone: "accent" };
    if (kind.startsWith("open_")) return { key: "done", label: "Opened", tone: "accent" };
    return { key: "done", label: "Completed", tone: "accent" };
  }
  if (message.cancelled) return { key: "cancelled", label: "Cancelled · nothing changed" };
  if (APPROVAL_KINDS.has(kind)) return { key: "approval", label: "Awaiting your approval", tone: "attn" };
  if (kind === "draft_email" || kind === "send_follow_up") return { key: "suggested", label: "Suggested · opens a draft" };
  return { key: "suggested", label: "Suggested" };
}
