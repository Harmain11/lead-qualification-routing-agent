export type GmailReviewNote = {
  outcome: "selected" | "skipped";
  title: string;
  detail: string;
};

function readableReviewText(value: string) {
  return value
    .replace(/<[^>]*>/g, "")
    .replace(/\b[0-9a-f]{24,}\b/gi, "")
    .replace(/_/g, " ")
    .replace(/\s+/g, " ")
    .replace(/^\s*[:;,.-]+\s*/, "")
    .replace(/\s*;\s*updated\.?$/i, "")
    .trim();
}

export function parseGmailReviewNotes(lastError: string | null) {
  if (!lastError) return { notes: [] as GmailReviewNote[], general: "" };
  const marker = "Participant review:";
  const markerIndex = lastError.indexOf(marker);
  if (markerIndex < 0) return { notes: [] as GmailReviewNote[], general: lastError };

  const general = lastError.slice(0, markerIndex).replace(/\bDetails:\s*$/i, "").trim();
  const entries = lastError.slice(markerIndex + marker.length).split(/\s*\|\s*/).map((entry) => entry.trim()).filter(Boolean);
  const notes = entries.map((entry): GmailReviewNote => {
    const match = entry.match(/^(?:Thread\s+\S+\s+)?(selected|skipped)\b\s*:?\s*(.*)$/i);
    if (!match) {
      return { outcome: "skipped", title: "Review note", detail: readableReviewText(entry) };
    }
    const outcome = match[1].toLowerCase() as GmailReviewNote["outcome"];
    const content = match[2].replace(/^:\s*/, "").trim();

    if (outcome === "selected") {
      const [identity = "", ...explanation] = content.split(";");
      const name = readableReviewText(identity.replace(/\s+as\s+.+$/i, ""));
      const detail = readableReviewText(explanation.filter((part) => !/^updated\.?$/i.test(part.trim())).join("; "));
      return {
        outcome,
        title: name ? `Lead identified · ${name}` : "Lead identified",
        detail,
      };
    }

    const [reason = "", ...explanation] = content.split(/:\s*/);
    const reasonLabels: Record<string, string> = {
      system_or_own_message: "Internal or system message",
      automated_sender: "Automated sender",
      bulk_or_transactional: "Bulk or transactional email",
      no_supported_prospect_candidate: "No supported prospect found",
    };
    return {
      outcome,
      title: reasonLabels[reason.trim().replace(/[.;]+$/, "")] || readableReviewText(reason),
      detail: readableReviewText(explanation.join(": ")),
    };
  });
  return { notes, general };
}
