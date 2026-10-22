const DIRECT_PHONE_CALL_PATTERNS = [
  /^\s*(?:please\s+)?(?:call|dial|phone)\s+(?!(?:history|transcript|logs?|records?|summary|notes?|details?|report|data|number)\b)[\w'-]+/i,
  /^\s*(?:please\s+)?(?:call|dial|phone)\s*[.!?]?\s*$/i,
  /\b(?:can|could|would)\s+(?:you|we|i|corral)\s+(?:please\s+)?(?:call|dial|phone|initiate|place|start|make)\b/i,
  /\b(?:i want|i need|i'd like|i would like)\b[\s\S]{0,40}\b(?:call|dial|phone)\b/i,
  /\b(?:make|place|start|initiate)\s+(?:me\s+)?(?:a\s+)?(?:phone\s+)?call\b/i,
  /\bhow\s+(?:do|can|would)\s+i\s+(?:call|dial|phone)\b/i,
];

export function isDirectPhoneCallRequest(text: string) {
  return DIRECT_PHONE_CALL_PATTERNS.some((pattern) => pattern.test(text));
}
