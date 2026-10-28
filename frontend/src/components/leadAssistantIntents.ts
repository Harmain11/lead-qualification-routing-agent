const FOLLOW_UP_EMAIL_DRAFT_REQUEST =
  /\b(?:write|draft|compose|prepare|create|make|send)\b[\s\S]{0,80}\b(?:follow[\s-]?up|e-?mail)\b|\b(?:follow[\s-]?up|e-?mail)\b[\s\S]{0,80}\b(?:write|draft|compose|prepare|create|make|send)\b/i;

export function isFollowUpEmailDraftRequest(text: string) {
  return FOLLOW_UP_EMAIL_DRAFT_REQUEST.test(text);
}
