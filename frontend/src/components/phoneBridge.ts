/** Only messages from the exact embedded provider window may change phone state. */
export const CLOUDTALK_ORIGIN = "https://phone.cloudtalk.io";
export const PHONE_CALL_ENDED = "corral:phone-call-ended";
export const cloudTalkEvents: Record<string, { label: string; active?: boolean }> = {
  ringing: { label: "A CloudTalk call is ringing.", active: true },
  dialing: { label: "CloudTalk is dialing.", active: true },
  calling: { label: "CloudTalk call connected.", active: true },
  hangup: { label: "Ending the CloudTalk call…", active: true },
  ended: { label: "CloudTalk call ended.", active: false },
  contact_info: { label: "CloudTalk contact details updated." },
};

export function readCloudTalkEvent(event: { origin: string; source: unknown; data: unknown }, frameWindow: unknown): string | null {
  if (!frameWindow || event.origin !== CLOUDTALK_ORIGIN || event.source !== frameWindow) return null;
  let payload = event.data;
  if (typeof payload === "string") {
    try { payload = JSON.parse(payload); } catch { return null; }
  }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return null;
  const name = (payload as { event?: unknown }).event;
  return typeof name === "string" && Object.hasOwn(cloudTalkEvents, name) ? name : null;
}
