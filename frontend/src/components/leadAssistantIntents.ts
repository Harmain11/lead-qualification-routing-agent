const FOLLOW_UP_EMAIL_DRAFT_REQUEST =
  /\b(?:write|draft|compose|prepare|create|make|send)\b[\s\S]{0,80}\b(?:follow[\s-]?up|e-?mail)\b|\b(?:follow[\s-]?up|e-?mail)\b[\s\S]{0,80}\b(?:write|draft|compose|prepare|create|make|send)\b/i;

export function isFollowUpEmailDraftRequest(text: string) {
  // Capability questions and safety checks can mention sending an email while
  // explicitly saying not to send one. They must stay in chat, not open a blank
  // composer as a misleading fallback.
  if (
    /\b(?:do not|don't|never)\b[\s\S]{0,60}\bsend(?:ing)?\s+(?:an?\s+)?e-?mail\b/i.test(text)
    && !/\b(?:draft|compose|write|prepare)\b/i.test(text)
  ) {
    return false;
  }
  return FOLLOW_UP_EMAIL_DRAFT_REQUEST.test(text);
}
