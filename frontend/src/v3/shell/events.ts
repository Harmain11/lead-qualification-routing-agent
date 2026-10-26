/** Window events the shell listens for, so any screen can ask for app-level actions. */

export type LeadAction = "open" | "call" | "email" | "quote" | "deal";

/** Open a lead at the right place: its calls, its email composer, or its deal room. */
export const LEAD_ACTION_EVENT = "corral:lead-action";
/** Toggle the phone (detail.open forces a state). */
export const PHONE_EVENT = "corral:phone";
/** Ask the Leads screen to open the New Lead sheet. */
export const NEW_LEAD_EVENT = "corral:new-lead";

export function leadAction(leadId: string, action: LeadAction) {
  window.dispatchEvent(new CustomEvent(LEAD_ACTION_EVENT, { detail: { leadId, action } }));
}

export function togglePhone(open?: boolean) {
  window.dispatchEvent(new CustomEvent(PHONE_EVENT, { detail: { open } }));
}
