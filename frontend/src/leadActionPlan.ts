type TalkingPointsRoute = {
  manual_review?: boolean;
  routing_reason?: string;
  action_plan?: { talking_points?: unknown };
};

export function getLeadTalkingPoints(route: TalkingPointsRoute | null | undefined): string[] {
  const configured = Array.isArray(route?.action_plan?.talking_points)
    ? route.action_plan.talking_points.filter(
        (point): point is string => typeof point === "string" && Boolean(point.trim()),
      )
    : [];
  if (configured.length || !route?.manual_review) return configured;

  const companyNameNeedsReview = /company name/i.test(route.routing_reason || "");
  return [
    companyNameNeedsReview
      ? "Confirm the company name from the original inquiry or other source evidence."
      : "Review the original inquiry and validate the details flagged for review.",
    "Separate confirmed facts from unknown details before choosing an outreach step.",
  ];
}
