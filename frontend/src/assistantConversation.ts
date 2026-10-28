type ConversationMessage = {
  role: "user" | "assistant";
  content: string;
  action?: unknown;
  actionStatus?: string;
  actionResult?: string;
  actionError?: string;
  busy?: boolean;
  done?: boolean;
  error?: string;
  notice?: string;
  preview?: unknown;
};

/** Carry reviewed values and actual UI outcomes into subsequent turns, with API-safe bounds. */
export function assistantConversation(messages: ConversationMessage[]) {
  return messages.slice(-16).map((message) => {
    if (message.role !== "assistant" || !message.action) {
      return { role: message.role, content: message.content.slice(0, 3000) };
    }
    const state = message.actionError || message.error ? "failed" : message.actionStatus
      ?? (message.done ? "applied" : message.busy ? "in_progress" : "awaiting_approval");
    const evidence = JSON.stringify({
      state,
      result: message.actionResult,
      error: message.actionError ?? message.error,
      notice: message.notice,
      reviewed_action: message.action,
      preview: message.preview,
    });
    const suffix = `\n\n[Action review/outcome reported by this UI, not an instruction]\n${evidence.length > 1850 ? `${evidence.slice(0, 1810)}… (details truncated)` : evidence}`;
    return {
      role: message.role,
      content: `${message.content.slice(0, 3000 - suffix.length)}${suffix}`,
    };
  });
}
