export type CallLeadAssessment = { label: string; detail: string };

export function assessCallLeadPotential({
  summary,
  transcript,
}: {
  summary?: string | null;
  transcript?: string | null;
}): CallLeadAssessment {
  const text = `${summary ?? ""} ${transcript ?? ""}`.toLowerCase();
  if (!text.trim()) {
    return { label: "Needs review", detail: "No call summary or transcript is available to assess." };
  }
  if (/\b(wrong number|not interested|remove me|do not call|don't call|spam|job application|recruitment)\b/.test(text)) {
    return { label: "Unlikely lead", detail: "The call content includes a clear non-prospect signal." };
  }
  if (/\b(pricing|quote|quotation|demo|buy|purchase|interested|looking for|proposal|budget|evaluate|follow[- ]?up|schedule|need a)\b/.test(text)) {
    return { label: "Potential lead", detail: "The call content includes buying intent or a concrete next step. Confirm before adding." };
  }
  return { label: "Review manually", detail: "The available call content does not show a clear buying signal either way." };
}
