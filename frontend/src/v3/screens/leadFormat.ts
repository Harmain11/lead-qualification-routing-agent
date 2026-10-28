import { titleize } from "../ui/primitives";

const PROVIDER_LABELS: Record<string, string> = { hubspot: "HubSpot", gohighlevel: "GoHighLevel", salesforce: "Salesforce" };
const SOURCE_LABELS: Record<string, string> = {
  email: "Email", website: "Website", web: "Web", offline: "Offline", unknown: "Unknown", purchased_list: "Purchased list",
  phone_inquiry: "Phone inquiry", partner_referral: "Partner referral", clutch: "Clutch", linkedin: "LinkedIn", manual: "Added manually",
  ...PROVIDER_LABELS,
};
export const providerLabel = (provider: string) => PROVIDER_LABELS[provider] ?? titleize(provider);
export const sourceLabel = (source: string) => SOURCE_LABELS[source] ?? titleize(source);
const ACRONYMS = new Set(["sdr", "ae", "bdr", "am", "csm"]);
export const queueLabel = (queue: string | null | undefined) =>
  queue
    ? queue.split(/[_\s-]+/).map((word) => (ACRONYMS.has(word.toLowerCase()) ? word.toUpperCase() : word.charAt(0).toUpperCase() + word.slice(1))).join(" ")
    : "Queue pending";
