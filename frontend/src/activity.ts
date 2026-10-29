import { createClientId } from "./clientIds";

export type ActivityToast = {
  id: string;
  message: string;
  kind: "success" | "error" | "info";
  action?: { label: string; run: () => void };
};

export function notifyActivity(
  message: string,
  kind: ActivityToast["kind"] = "success",
  action?: ActivityToast["action"],
) {
  try {
    window.dispatchEvent(new CustomEvent<ActivityToast>("corral:activity", {
      detail: { id: createClientId(), message, kind, action },
    }));
  } catch {
    // Feedback is optional; it must not reject an already-successful mutation.
    console.warn("The action completed, but its status notification could not be displayed.");
  }
}

export const toast = {
  success: (message: string, action?: ActivityToast["action"]) => notifyActivity(message, "success", action),
  error: (message: string, action?: ActivityToast["action"]) => notifyActivity(message, "error", action),
  info: (message: string, action?: ActivityToast["action"]) => notifyActivity(message, "info", action),
};

const IN_PROGRESS = new Set(["running", "queued", "pending", "sending", "processing", "awaiting_authorization", "started"]);
const FAILED = new Set(["failed", "error", "delivery_uncertain", "blocked"]);
const FINISHED = new Set(["completed", "succeeded", "success", "synced", "sent", "imported", "done"]);

/**
 * Feedback after a successful (2xx) mutation. It reports only what the response
 * actually says: work that is still running is never described as finished.
 */
export function mutationStatusMessage(url: string, result: unknown): { message: string; kind: ActivityToast["kind"] } {
  const value = result && typeof result === "object" ? result as Record<string, unknown> : {};
  const status = String(value.status ?? "").toLowerCase();
  const serverMessage = typeof value.message === "string" && value.message.trim() ? value.message.trim() : null;
  if (value.verified === false || FAILED.has(status)) {
    return {
      message: serverMessage || String(value.error_message || "This needs attention. Check its status before trying again."),
      kind: "error",
    };
  }
  if (url.includes("/oauth/begin")) {
    return { message: "Opening the provider's sign-in. Choose the account to connect.", kind: "info" };
  }
  if (IN_PROGRESS.has(status)) {
    return { message: serverMessage || "Started. The result appears here when it finishes.", kind: "info" };
  }
  if (url.endsWith("/send")) {
    return status === "sent"
      ? { message: "Email sent.", kind: "success" }
      : { message: "Email handed to your mail provider. Check Sent to confirm delivery.", kind: "info" };
  }
  if (url.includes("/test") || url.includes("/verify")) {
    return value.verified === true || ["connected", "verified"].includes(status)
      ? { message: "Connection verified.", kind: "success" }
      : { message: "Verification requested. The connection status updates when the provider responds.", kind: "info" };
  }
  if (url.includes("/sync") || url.includes("/import")) {
    const noun = url.includes("/sync") ? "Sync" : "Import";
    return FINISHED.has(status)
      ? { message: `${noun} finished.`, kind: "success" }
      : { message: `${noun} started. The result appears here when it finishes.`, kind: "info" };
  }
  if (url.includes("/disconnect")) return { message: "Disconnected.", kind: "success" };
  if (url.startsWith("/v1/email/drafts")) return { message: "Draft saved.", kind: "success" };
  if (url.includes("/draft")) return { message: "Draft ready. Review and edit it before sending.", kind: "success" };
  return { message: serverMessage || "Saved.", kind: "success" };
}
