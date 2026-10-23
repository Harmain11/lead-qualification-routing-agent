const SOURCE_LABELS: Record<string, string> = {
  manual: "Manually added",
  website: "Web form",
  web: "Web",
  email: "Email",
  offline: "Offline",
  unknown: "Unknown",
  purchased_list: "Purchased list",
  phone_inquiry: "Phone inquiry",
  partner_referral: "Partner referral",
  clutch: "Clutch",
  salesforce: "Salesforce",
  hubspot: "HubSpot",
  gohighlevel: "GoHighLevel",
};

export function leadSourceCategory(source: string, sourceDetail?: string | null) {
  return source === "manual" || sourceDetail === "manual-control-center"
    ? "manual"
    : source;
}

export function leadSourceLabel(source: string, sourceDetail?: string | null) {
  const category = leadSourceCategory(source, sourceDetail);
  return (
    SOURCE_LABELS[category] ??
    category.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase())
  );
}

export function leadSourceDetailLabel(sourceDetail?: string | null) {
  if (sourceDetail === "manual-control-center") return "Added in Corral";
  return sourceDetail?.trim() || "Original intake";
}

export function leadSourceDisplay(source: string, sourceDetail?: string | null) {
  if (sourceDetail === "manual-control-center") return "Manually added";
  return sourceDetail?.trim() || leadSourceLabel(source);
}
