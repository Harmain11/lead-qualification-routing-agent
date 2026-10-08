export type ActivityToast = {
  id: string;
  message: string;
  kind: "success" | "error" | "info";
};

export function notifyActivity(message: string, kind: ActivityToast["kind"] = "success") {
  window.dispatchEvent(new CustomEvent<ActivityToast>("corral:activity", {
    detail: { id: crypto.randomUUID(), message, kind },
  }));
}

export function mutationStatusMessage(url: string, result: unknown): { message: string; kind: ActivityToast["kind"] } {
  const value = result && typeof result === "object" ? result as Record<string, unknown> : {};
  if (value.verified === false || ["failed", "error", "delivery_uncertain"].includes(String(value.status))) {
    return { message: String(value.message || value.error_message || "The action needs attention. Check its status before retrying."), kind: "error" };
  }
  if (["running", "queued", "pending", "sending", "awaiting_authorization"].includes(String(value.status)) || url.includes("/oauth/begin")) {
    return { message: url.includes("/oauth/begin") ? "Opening provider sign-in. Choose the account you want to connect." : "Action started. Its final status will appear when processing finishes.", kind: "info" };
  }
  if (url.endsWith("/send")) return { message: "Email sent successfully.", kind: "success" };
  if (url.includes("/test") || url.includes("/verify")) return { message: "Connection verified successfully.", kind: "success" };
  if (url.includes("/disconnect")) return { message: "Account disconnected.", kind: "success" };
  if (url.includes("/sync")) return { message: "Synchronization completed successfully.", kind: "success" };
  if (url.includes("/import")) return { message: "Import completed successfully.", kind: "success" };
  if (url.includes("/draft")) return { message: "AI draft created. Review and edit it before sending.", kind: "success" };
  return { message: "Changes saved successfully.", kind: "success" };
}
