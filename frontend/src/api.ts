import { mutationStatusMessage, notifyActivity } from "./activity";
import { createClientId } from "./clientIds";
import { recentReviewBatch } from "./inboundReview";
import type {
  AdvancedAnalytics,
  AgentActivity,
  ChannelAnalytics,
  CrmSync,
  CrmInboundSyncAccepted,
  CrmInboundSyncRun,
  CrmInboundSyncState,
  DashboardAnalytics,
  ExtendedAnalytics,
  IntegrationCard,
  LeadApprovalPolicy,
  Lead,
  LeadAssignmentOption,
  LeadPipelineEvent,
  LeadProgressResult,
  GmailConversationSyncResult,
  MeetingRecommendations,
  MeetingSuggestion,
  WorkspaceCalendarEvent,
  ConfirmMeetingResult,
  RetryConfirmedMeetingBookingResult,
  LeadListResponse,
  LeadPayload,
  FollowUpEmail,
  ManualReviewAnalytics,
  PipelineAnalytics,
  ProviderConnection,
  RepAnalytics,
  RevenueAnalytics,
  RoutingConfig,
  PublicIntakeConfig,
  TimeRange,
  ReasoningUsage,
  ReasoningUsagePeriod,
  TimeSeriesAnalytics,
  AuthSession,
  PermissionOverrides,
  WorkspaceAuditEvent,
  WorkspaceInvitation,
  WorkspaceMember,
  IcpConfiguration,
  IcpPreview,
  IcpValidation,
  IcpVersion,
  SlackNotificationConfig,
  WorkspaceEmailTemplate,
  GmailMailboxMessagePage,
  GmailThreadMessage,
  GmailSendRequest,
  GmailSendResult,
  GmailSendAccount,
} from "./types";

export const DEMO_WORKSPACE_ID = "00000000-0000-0000-0000-000000000006";
const CSRF_COOKIE_NAME = import.meta.env.VITE_AUTH_CSRF_COOKIE_NAME || "lead_agent_csrf";
// Keep the standard server cookie as a fallback. This lets local previews
// recover when a stale Vite environment overrides the frontend cookie name.
const CSRF_COOKIE_NAMES = Array.from(new Set(["lead_agent_csrf", CSRF_COOKIE_NAME]));
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");
let activeWorkspaceId = import.meta.env.VITE_WORKSPACE_ID || DEMO_WORKSPACE_ID;
const inFlightGets = new Map<string, Promise<unknown>>();

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "ApiError";
  }
}

export type LeadAssistantAction = {
  kind: "none" | "update_profile" | "add_note" | "record_progress" | "draft_email" | "send_follow_up" | "refresh_email" | "sync_crm" | "delete_lead" | "reevaluate_lead" | "complete_manual_review" | "open_email" | "open_meetings" | "open_dialer" | "open_closing" | "open_history";
  profile_updates: Partial<Pick<Lead, "full_name" | "email" | "phone" | "job_title" | "company_name" | "industry" | "employee_count" | "annual_revenue" | "geography" | "budget" | "product_or_service">>;
  clear_fields: string[];
  note: string | null;
  progress_stage: "contacted" | "responded" | "meeting_scheduled" | "proposal_sent" | "converted" | "lost" | null;
  progress_note: string | null;
  conversion_type: "project" | "sale" | null;
  closed_revenue: number | null;
  credited_user_id: string | null;
  email_subject: string | null;
  email_body: string | null;
  crm_provider: string | null;
};

export type LeadAssistantReply = {
  reply: string;
  action: LeadAssistantAction;
  lead_links?: WorkspaceAssistantLeadLink[];
};

export type WorkspaceAssistantArea =
  | "analytics"
  | "agents"
  | "automations"
  | "icp"
  | "settings"
  | "integrations"
  | "team"
  | "dialer";

export type AssignmentNotification = {
  id: string;
  lead_id: string;
  lead_name: string;
  company_name: string;
  kind: "assignment" | "meeting_reply_draft" | string;
  message: string | null;
  created_at: string;
  read_at: string | null;
};

export type WorkspaceAssistantAction = {
  kind:
    | "none"
    | "open_module"
    | "open_lead"
    | "open_deal_desk"
    | "create_lead"
    | "set_automation_active"
    | "create_icp_draft"
    | "update_routing_config"
    | "set_lead_confirmation"
    | "disconnect_integration"
    | "invite_member"
    | "update_member_role"
    | "update_member_permissions"
    | "suspend_member"
    | "reactivate_member"
    | "send_email"
    | "update_lead_profile"
    | "add_lead_note"
    | "record_lead_progress"
    | "reevaluate_lead"
    | "complete_lead_manual_review";
  area: WorkspaceAssistantArea | null;
  title: string;
  summary: string;
  target_id: string | null;
  target_label: string | null;
  email: string | null;
  email_recipient: string | null;
  email_cc: string | null;
  email_bcc: string | null;
  email_subject: string | null;
  email_body: string | null;
  email_html_body?: string | null;
  role: string | null;
  enabled: boolean | null;
  payload_json: string | null;
};

export type WorkspaceAssistantReply = {
  reply: string;
  action: WorkspaceAssistantAction;
  lead_links?: WorkspaceAssistantLeadLink[];
  context_warnings?: string[];
  drafting_context?: Record<string, unknown>;
  conversation_id?: string | null;
};

export type WorkspaceAssistantLeadLink = {
  id: string;
  name: string;
  company: string | null;
  suggested: boolean;
};

export type WorkspaceAgentConversationMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  action?: WorkspaceAssistantAction;
  actionStatus?: "applying" | "sending" | "applied" | "dismissed";
  actionResult?: string;
  actionError?: string;
  contextWarnings?: string[];
  leadLinks?: WorkspaceAssistantLeadLink[];
  needsVerification?: boolean;
};

export type WorkspaceAgentConversationSummary = {
  id: string;
  title: string;
  updated_at: string;
  message_count: number;
  preview: string;
};

export function setActiveWorkspace(workspaceId: string) {
  activeWorkspaceId = workspaceId;
}

export function getActiveWorkspace() {
  return activeWorkspaceId;
}

export function getRoutingConfig(): Promise<RoutingConfig> {
  return request<RoutingConfig>("/v1/routing/config");
}

export function updateRoutingConfig(
  member_percentages?: Record<string, number>,
  manual_review?: RoutingConfig["manual_review"],
): Promise<RoutingConfig> {
  const body: {
    member_percentages?: Record<string, number>;
    manual_review?: RoutingConfig["manual_review"];
  } = {};
  if (member_percentages) body.member_percentages = member_percentages;
  if (manual_review) body.manual_review = manual_review;
  return request<RoutingConfig>("/v1/routing/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function getPublicIntakeConfig(): Promise<PublicIntakeConfig> {
  return request<PublicIntakeConfig>("/v1/intake/public-config");
}

export function rotatePublicIntakeConfig(): Promise<PublicIntakeConfig> {
  return request<PublicIntakeConfig>("/v1/intake/public-config/rotate", { method: "POST" });
}

export function apiUrl(path: string) {
  return path.startsWith("/") ? `${API_BASE_URL}${path}` : path;
}

function csrfToken(): string | null {
  for (const cookieName of CSRF_COOKIE_NAMES) {
    const value = document.cookie
      .split("; ")
      .find((item) => item.startsWith(`${cookieName}=`))
      ?.split("=", 2)[1];
    if (value) return decodeURIComponent(value);
  }
  return null;
}

function withWorkspace(headers?: HeadersInit): Headers {
  const result = new Headers(headers);
  result.set("X-Workspace-ID", activeWorkspaceId);
  const csrf = csrfToken();
  if (csrf) result.set("X-CSRF-Token", csrf);
  return result;
}

export async function request<T>(
  url: string,
  options?: RequestInit,
  feedback: { activityToast?: boolean } = {},
): Promise<T> {
  const method = options?.method?.toUpperCase() ?? "GET";
  const key = method === "GET" && !options?.signal ? `${activeWorkspaceId}:${url}` : null;
  if (key) {
    const existing = inFlightGets.get(key);
    if (existing) return existing as Promise<T>;
  }

  const pending = performRequest<T>(url, options, feedback.activityToast !== false);
  if (!key) return pending;
  inFlightGets.set(key, pending);
  try {
    return await pending;
  } finally {
    if (inFlightGets.get(key) === pending) inFlightGets.delete(key);
  }
}

async function performRequest<T>(
  url: string,
  options?: RequestInit,
  showActivityToast = true,
): Promise<T> {
  const response = await fetch(apiUrl(url), {
    ...options,
    credentials: "include",
    headers: withWorkspace(options?.headers),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: "Request failed" }));
    const detail = typeof body.detail === "string"
      ? body.detail
      : Array.isArray(body.detail)
        ? body.detail
          .map((item: unknown) => {
            if (!item || typeof item !== "object" || !("msg" in item)) return null;
            return typeof item.msg === "string" ? item.msg : null;
          })
          .filter((message: string | null): message is string => Boolean(message))
          .join("; ")
        : null;
    const message = detail
      || (response.status === 401
        ? "Your session expired or the credentials were not accepted."
        : response.status === 403
          ? "You do not have permission to perform this action."
          : `Request failed (${response.status}). Please try again.`);
    throw new ApiError(message, response.status);
  }
  const result = response.status === 204 ? undefined : await response.json();
  const method = options?.method?.toUpperCase() ?? "GET";
  if (showActivityToast && !["GET", "HEAD", "OPTIONS"].includes(method) && !url.startsWith("/v1/auth/")) {
    const status = mutationStatusMessage(url, result);
    notifyActivity(status.message, status.kind);
  }
  return result as T;
}

export const getSession = () => request<AuthSession | null>("/v1/auth/status");

export type AccountProfile = {
  id: string;
  email: string;
  display_name: string;
  job_title: string | null;
  phone: string | null;
  bio: string | null;
  avatar_data_url: string | null;
};

export type AccountProfileUpdate = Omit<AccountProfile, "id" | "email">;

export const getAccountProfile = () => request<AccountProfile>("/v1/auth/profile");
export const updateAccountProfile = (profile: AccountProfileUpdate) =>
  request<AccountProfile>("/v1/auth/profile", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(profile),
  });

export const getEmailTemplates = () => request<WorkspaceEmailTemplate[]>("/v1/settings/email-templates");
export const saveEmailTemplate = (kind: WorkspaceEmailTemplate["kind"], template: Omit<WorkspaceEmailTemplate, "kind">) =>
  request<WorkspaceEmailTemplate>(`/v1/settings/email-templates/${kind}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(template),
  });
export const generateEmailTemplate = (
  kind: WorkspaceEmailTemplate["kind"],
  sample: Partial<Pick<WorkspaceEmailTemplate, "subject" | "body">> & {
    generation_mode?: "style" | "fresh";
    important_context?: string;
  },
) =>
  request<WorkspaceEmailTemplate>(`/v1/settings/email-templates/${kind}/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(sample),
  });

export type LeadConfirmationAutomation = { enabled: boolean };
export type GoogleCalendarBookingLink = { url: string };
export type LeadConfirmationSource = {
  source: string;
  label: string;
  enabled: boolean;
  uses_default: boolean;
};
export type LeadConfirmationSources = {
  default_enabled: boolean;
  sources: LeadConfirmationSource[];
};
export const getLeadConfirmationAutomation = () =>
  request<LeadConfirmationAutomation>(
    "/v1/settings/email-automation/lead-confirmations",
  );
export const setLeadConfirmationAutomation = (enabled: boolean) =>
  request<LeadConfirmationAutomation>(
    "/v1/settings/email-automation/lead-confirmations",
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    },
  );
export const getGoogleCalendarBookingLink = () =>
  request<GoogleCalendarBookingLink>(
    "/v1/settings/email-automation/google-calendar-booking-link",
  );
export const setGoogleCalendarBookingLink = (url: string) =>
  request<GoogleCalendarBookingLink>(
    "/v1/settings/email-automation/google-calendar-booking-link",
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    },
  );
export const getLeadConfirmationSources = () =>
  request<LeadConfirmationSources>(
    "/v1/settings/email-automation/lead-confirmations/sources",
  );
export const setLeadConfirmationSource = (
  source: string,
  enabled: boolean | null,
) =>
  request<LeadConfirmationSources>(
    `/v1/settings/email-automation/lead-confirmations/sources/${encodeURIComponent(source)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    },
  );

export async function signIn(email: string, password: string): Promise<AuthSession> {
  const session = await request<AuthSession>("/v1/auth/signin", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  setActiveWorkspace(session.current_workspace_id);
  return session;
}

export async function signInWithWorkspaceCode(input: {
  workspace_id: string;
  code: string;
  email: string;
  display_name: string;
  password: string | null;
}): Promise<AuthSession> {
  const session = await request<AuthSession>("/v1/auth/workspace-code", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  setActiveWorkspace(session.current_workspace_id);
  return session;
}

export async function signUp(input: {
  email: string;
  password: string;
  display_name: string;
  workspace_name: string;
}): Promise<{ message: string }> {
  return request<{ message: string }>("/v1/auth/signup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export const getPasswordSignupStatus = () =>
  request<{ enabled: boolean }>("/v1/auth/password-signup/status");

export const resendVerification = (email: string) =>
  request<{ message: string }>("/v1/auth/resend-verification", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });

export function startGoogleAuth(invitationToken?: string): void {
  const query = invitationToken
    ? `?invitation_token=${encodeURIComponent(invitationToken)}`
    : "";
  window.location.assign(apiUrl(`/v1/auth/google/start${query}`));
}

export async function startDevelopmentSession(): Promise<AuthSession> {
  const session = await request<AuthSession>("/v1/auth/development-session", { method: "POST" });
  setActiveWorkspace(session.current_workspace_id);
  return session;
}

export const signOut = () => request<void>("/v1/auth/signout", { method: "POST" });

export async function changeWorkspace(workspaceId: string): Promise<AuthSession> {
  const session = await request<AuthSession>("/v1/auth/workspace", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ workspace_id: workspaceId }),
  });
  setActiveWorkspace(session.current_workspace_id);
  return session;
}

export async function createLead(payload: Partial<LeadPayload>): Promise<Lead> {
  return request<Lead>("/v1/leads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export type LeadListQuery = {
  limit?: number;
  offset?: number;
  search?: string;
  source?: string;
  qualification_band?: string;
  date_from?: string;
  date_to?: string;
};

export async function listLeads(query: LeadListQuery = {}, signal?: AbortSignal): Promise<LeadListResponse> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return request<LeadListResponse>(`/v1/leads${suffix}`, { signal });
}

export const LEAD_EXPORT_COLUMNS = [
  { key: "lead_name", label: "Lead / Contact Name", defaultSelected: true },
  { key: "company", label: "Company", defaultSelected: true },
  { key: "email", label: "Email", defaultSelected: true },
  { key: "source", label: "Source", defaultSelected: true },
  { key: "received_date", label: "Received Date", defaultSelected: true },
  { key: "service_interest", label: "Service / Interest", defaultSelected: true },
  { key: "icp_score", label: "ICP Score", defaultSelected: true },
  { key: "intent_score", label: "Intent Score", defaultSelected: true },
  { key: "engagement_score", label: "Engagement Score", defaultSelected: true },
  { key: "total_score", label: "Total Score", defaultSelected: true },
  { key: "qualification_band", label: "Qualification Band", defaultSelected: true },
  { key: "icp_version", label: "ICP Version", defaultSelected: true },
  { key: "routing_queue", label: "Routing Queue", defaultSelected: true },
  { key: "assigned_representative", label: "Assigned Representative", defaultSelected: true },
  { key: "priority", label: "Priority / Urgency", defaultSelected: true },
  { key: "crm_provider", label: "CRM Provider", defaultSelected: true },
  { key: "crm_sync_state", label: "CRM Sync State", defaultSelected: true },
  { key: "provider_record_id", label: "Provider Record ID", defaultSelected: true },
  { key: "opportunity_value", label: "Opportunity Value", defaultSelected: true },
  { key: "revenue_value", label: "Revenue Value", defaultSelected: true },
  { key: "summary", label: "AI / Grounded Summary", defaultSelected: true },
  { key: "recommended_action", label: "Recommended Action / Action Plan", defaultSelected: true },
  { key: "phone", label: "Phone", defaultSelected: false },
  { key: "job_title", label: "Job Title", defaultSelected: false },
  { key: "industry", label: "Industry", defaultSelected: false },
  { key: "employee_count", label: "Employee Count", defaultSelected: false },
  { key: "annual_revenue", label: "Company Annual Revenue", defaultSelected: false },
  { key: "geography", label: "Geography", defaultSelected: false },
  { key: "budget", label: "Budget", defaultSelected: false },
  { key: "source_detail", label: "Source Detail", defaultSelected: false },
  { key: "inquiry_message", label: "Original Inquiry", defaultSelected: false },
] as const;

export type LeadExportColumn = (typeof LEAD_EXPORT_COLUMNS)[number]["key"];

export type LeadExportInput = {
  format: "csv" | "xlsx" | "pdf";
  scope: "current_filters" | "selected" | "all";
  filters: {
    search?: string;
    source?: string | null;
    qualification_bands?: string[];
    date_from?: string | null;
    date_to?: string | null;
  };
  selected_lead_ids?: string[];
  columns?: LeadExportColumn[];
};

export async function exportLeads(input: LeadExportInput): Promise<{ blob: Blob; filename: string }> {
  const response = await fetch(apiUrl("/v1/leads/export"), {
    method: "POST",
    credentials: "include",
    headers: withWorkspace({ "Content-Type": "application/json" }),
    body: JSON.stringify(input),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: "Export failed" }));
    throw new Error(typeof body.detail === "string" ? body.detail : "Export failed");
  }
  const disposition = response.headers.get("Content-Disposition") || "";
  const filename = disposition.match(/filename="([^"]+)"/)?.[1] || `corral-leads-${new Date().toISOString().slice(0, 10)}.${input.format}`;
  const blob = await response.blob();
  notifyActivity("Export prepared successfully.");
  return { blob, filename };
}

export function getCrmSyncHistory(leadId: string, signal?: AbortSignal): Promise<CrmSync[]> {
  return request(`/v1/leads/${leadId}/crm-sync/history`, { signal });
}

async function jsonRequest<T>(
  url: string,
  options?: RequestInit,
  feedback: { activityToast?: boolean } = {},
): Promise<T> {
  return request<T>(url, options, feedback);
}

export function listIntegrations(): Promise<IntegrationCard[]> {
  return jsonRequest<IntegrationCard[]>("/v1/integrations");
}

export function listLeadApprovalPolicies(): Promise<LeadApprovalPolicy[]> {
  return jsonRequest<LeadApprovalPolicy[]>("/v1/integrations/lead-approval-policies");
}

export function updateLeadApprovalPolicy(
  provider: string,
  autoApproveLeads: boolean,
): Promise<LeadApprovalPolicy> {
  return jsonRequest<LeadApprovalPolicy>(
    `/v1/integrations/lead-approval-policies/${encodeURIComponent(provider)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ auto_approve_leads: autoApproveLeads }),
    },
  );
}

export function listGmailMailboxMessages(
  connectionId: string,
  folder: "inbox" | "sent" | "all" | "drafts" | "spam" | "trash" | "archive" = "inbox",
  query = "",
  pageToken?: string | null,
  signal?: AbortSignal,
): Promise<GmailMailboxMessagePage> {
  const params = new URLSearchParams({ connection_id: connectionId, folder, query });
  if (pageToken) params.set("page_token", pageToken);
  return request(`/v1/email/messages?${params.toString()}`, { signal });
}

export function getGmailMailboxThread(
  connectionId: string,
  threadId: string,
  signal?: AbortSignal,
): Promise<{ messages: GmailThreadMessage[] }> {
  const params = new URLSearchParams({ connection_id: connectionId, thread_id: threadId });
  return request(`/v1/email/thread?${params.toString()}`, { signal });
}

export function sendGmailEmail(payload: GmailSendRequest): Promise<GmailSendResult> {
  return request("/v1/email/send", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function listWorkspaceEmailDrafts(): Promise<import("./types").WorkspaceEmailDraftSummary[]> {
  return request("/v1/email/drafts");
}

export function getWorkspaceEmailDraft(draftId: string): Promise<import("./types").WorkspaceEmailDraft> {
  return request(`/v1/email/drafts/${draftId}`);
}

export function saveWorkspaceEmailDraft(
  payload: import("./types").WorkspaceEmailDraftInput,
  draftId?: string,
): Promise<import("./types").WorkspaceEmailDraft> {
  return request(draftId ? `/v1/email/drafts/${draftId}` : "/v1/email/drafts", {
    method: draftId ? "PUT" : "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function deleteWorkspaceEmailDraft(draftId: string): Promise<void> {
  return request(`/v1/email/drafts/${draftId}`, { method: "DELETE" }, { activityToast: false });
}

export function listGmailSendAccounts(): Promise<GmailSendAccount[]> {
  return request("/v1/email/send-accounts");
}

export function getInboundSyncState(connectionId: string, provider: string = "salesforce"): Promise<CrmInboundSyncState> {
  const objectType = ["hubspot", "gohighlevel"].includes(provider) ? "contacts" : ["gmail", "outlook"].includes(provider) ? "message" : "Lead";
  return jsonRequest(`/v1/integrations/connections/${connectionId}/inbound-sync/state?object_type=${objectType}`);
}

export function configureInboundSync(
  connectionId: string,
  enabled: boolean,
  provider: string = "salesforce",
  intervalSeconds?: number,
): Promise<CrmInboundSyncState> {
  return jsonRequest(`/v1/integrations/connections/${connectionId}/inbound-sync/configuration`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      enabled,
      object_type: ["hubspot", "gohighlevel"].includes(provider) ? "contacts" : ["gmail", "outlook"].includes(provider) ? "message" : "Lead",
      interval_seconds: intervalSeconds ?? (provider === "gmail" ? 300 : 60),
    }),
  });
}

export function runInboundSync(
  connectionId: string,
  mode: "initial" | "incremental" | "manual" | "review_recent" | "review_previous",
  provider: string = "salesforce",
  reviewLimit = 100,
): Promise<CrmInboundSyncRun | CrmInboundSyncAccepted> {
  const review = ["review_recent", "review_previous"].includes(mode) ? recentReviewBatch(reviewLimit) : null;
  return jsonRequest<CrmInboundSyncRun | CrmInboundSyncAccepted>(`/v1/integrations/connections/${connectionId}/inbound-sync`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode, object_type: ["hubspot", "gohighlevel"].includes(provider) ? "contacts" : ["gmail", "outlook"].includes(provider) ? "message" : "Lead", limit: review?.limit ?? (["gmail", "outlook"].includes(provider) ? 100 : 500), page_size: review?.pageSize ?? 100, send_confirmation_email: !["review_recent", "review_previous"].includes(mode) }),
  });
}

export function getInboundSyncRuns(connectionId: string): Promise<CrmInboundSyncRun[]> {
  return jsonRequest(`/v1/integrations/connections/${connectionId}/inbound-sync/runs?limit=10`);
}

export function cancelInboundSync(
  connectionId: string,
): Promise<{ status: "cancellation_requested" | "not_running"; message: string }> {
  return jsonRequest(
    `/v1/integrations/connections/${connectionId}/inbound-sync/cancel?object_type=message`,
    { method: "POST" },
  );
}

export function sendLeadToCrm(leadId: string, provider?: string): Promise<CrmSync> {
  const query = provider ? `?provider=${encodeURIComponent(provider)}` : "";
  return jsonRequest(`/v1/leads/${leadId}/crm-sync${query}`, { method: "POST" });
}

export function retryLeadNotification(leadId: string): Promise<import("./types").Notification> {
  return jsonRequest(`/v1/leads/${leadId}/notifications/retry`, { method: "POST" });
}

export function updateLeadTitle(leadId: string, jobTitle: string): Promise<Lead> {
  return jsonRequest(`/v1/leads/${leadId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_title: jobTitle }),
  });
}

export function updateLeadGenerated(
  leadId: string,
  isGenerated: boolean,
): Promise<Lead> {
  return jsonRequest<Lead>(`/v1/leads/${leadId}/generated`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ is_generated: isGenerated }),
  });
}

export function updateLeadAssignment(
  leadId: string,
  assignedRepId: string | null,
): Promise<Lead> {
  return jsonRequest<Lead>(`/v1/leads/${leadId}/assignment`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ assigned_rep_id: assignedRepId }),
  });
}

export function listLeadAssignmentOptions(
  signal?: AbortSignal,
): Promise<LeadAssignmentOption[]> {
  return jsonRequest<LeadAssignmentOption[]>("/v1/leads/assignment-options", {
    signal,
  });
}

export function updateLeadProfile(
  leadId: string,
  updates: LeadAssistantAction["profile_updates"],
): Promise<Lead> {
  return jsonRequest<Lead>(`/v1/leads/${leadId}/profile`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
}

export function reevaluateLead(leadId: string): Promise<Lead> {
  return jsonRequest<Lead>(`/v1/leads/${leadId}/reevaluate`, {
    method: "POST",
  });
}

export function completeLeadManualReview(leadId: string): Promise<Lead> {
  return jsonRequest<Lead>(`/v1/leads/${leadId}/manual-review/complete`, {
    method: "POST",
  });
}

export function chatWithLeadAssistant(
  leadId: string,
  messages: Array<{ role: "user" | "assistant"; content: string }>,
): Promise<LeadAssistantReply> {
  return jsonRequest<LeadAssistantReply>(`/v1/leads/${leadId}/assistant/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages }),
  });
}

export function chatWithWorkspaceAssistant(
  messages: Array<{ role: "user" | "assistant"; content: string }>,
  context: Record<string, unknown> = {},
  conversationId: string | null = null,
  messageCount = messages.length,
): Promise<WorkspaceAssistantReply> {
  return jsonRequest<WorkspaceAssistantReply>("/v1/agent/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      messages,
      context,
      conversation_id: conversationId,
      message_count: messageCount,
    }),
  });
}

export function listWorkspaceAgentConversations(): Promise<{ items: WorkspaceAgentConversationSummary[] }> {
  return jsonRequest("/v1/agent/conversations");
}

export function createWorkspaceAgentConversation(): Promise<{ id: string; title: string; messages: WorkspaceAgentConversationMessage[] }> {
  return jsonRequest("/v1/agent/conversations", { method: "POST" }, { activityToast: false });
}

export function getWorkspaceAgentConversation(id: string): Promise<{
  id: string;
  title: string;
  messages: WorkspaceAgentConversationMessage[];
}> {
  return jsonRequest(`/v1/agent/conversations/${encodeURIComponent(id)}`);
}

export function saveWorkspaceAgentConversation(
  id: string,
  messages: WorkspaceAgentConversationMessage[],
): Promise<{ id: string; title: string; message_count: number }> {
  return jsonRequest(`/v1/agent/conversations/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages }),
  }, { activityToast: false });
}

export function deleteWorkspaceAgentConversation(id: string): Promise<void> {
  return jsonRequest(`/v1/agent/conversations/${encodeURIComponent(id)}`, {
    method: "DELETE",
  }, { activityToast: false });
}

export const LEAD_NOTE_SAVED_EVENT = "corral:lead-note-saved";

export async function saveLeadNote(leadId: string, body: string): Promise<{ id: string; body: string }> {
  const text = body.trim();
  if (!text || text.length > 5000) throw new Error("Enter a note between 1 and 5,000 characters.");
  const noteId = createClientId();
  const result = await jsonRequest<{ id: string; body: string }>(`/v1/collaboration/leads/${leadId}/notes/${noteId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ body: text, version: 0 }),
  });
  // Refresh visible notes immediately, but optional browser feedback must never
  // turn a successful write into an apparent failure.
  try {
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent(LEAD_NOTE_SAVED_EVENT, { detail: { leadId } }));
    }
  } catch { /* The persisted note remains saved. */ }
  return result;
}

export function deleteLead(leadId: string): Promise<void> {
  return jsonRequest<void>(`/v1/leads/${leadId}`, { method: "DELETE" });
}

export function bulkDeleteLeads(leadIds: string[]): Promise<{ requested: number; deleted: number }> {
  return jsonRequest(`/v1/leads/bulk-delete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lead_ids: leadIds }),
  });
}

export function bulkSendLeadsToCrm(leadIds: string[], provider: string): Promise<{
  requested: number;
  succeeded: number;
  failed: number;
  results: Array<{ lead_id: string; status: string; error?: string | null }>;
}> {
  return jsonRequest(`/v1/leads/bulk-crm-sync?provider=${encodeURIComponent(provider)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lead_ids: leadIds, confirmed: true }),
  });
}

export function beginIntegration(provider: string): Promise<{ connection: ProviderConnection; authorization_url: string; state: string }> {
  return jsonRequest(`/v1/integrations/${provider}/connect`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ account_name: `Pending ${provider} account`, scopes: [] }),
  });
}

export function addIntegrationCredential(provider: string, accountName: string, authType: string, scopes: string[], credentials: Record<string, string>, metadata: Record<string, unknown> = {}): Promise<ProviderConnection> {
  return jsonRequest(`/v1/integrations/${provider}/credentials`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ account_name: accountName, auth_type: authType, scopes, credentials, metadata, activate: true }),
  });
}

export function testIntegration(connectionId: string): Promise<{ connection: ProviderConnection; valid: boolean; verified: boolean; status: string; message: string; retryable: boolean; error_code: string | null; backfill_job_id?: string | null }> {
  return jsonRequest(`/v1/integrations/connections/${connectionId}/test`, { method: "POST" });
}

export function backfillOpenAI(connectionId: string): Promise<{ status: string; job_id: string; queued_count: number; message: string }> {
  return jsonRequest(`/v1/integrations/connections/${connectionId}/backfill-openai`, { method: "POST" });
}

export type ReasoningBackfillStatus = {
  job_id: string;
  status: "queued" | "running" | "completed" | "partial_failure" | "failed" | "no_eligible_leads" | string;
  total: number;
  completed: number;
  failed: number;
  skipped: number;
  provider: string;
  model: string;
};

export function backfillReasoning(connectionId: string): Promise<{ status: string; job_id: string; queued_count: number; message: string }> {
  return jsonRequest(`/v1/integrations/connections/${connectionId}/backfill-reasoning`, { method: "POST" });
}

export function getReasoningBackfillStatus(connectionId: string, jobId: string): Promise<ReasoningBackfillStatus> {
  return jsonRequest(`/v1/integrations/connections/${connectionId}/backfill-reasoning/${encodeURIComponent(jobId)}`);
}

export function activateReasoningProvider(connectionId: string): Promise<ProviderConnection> {
  return jsonRequest(`/v1/integrations/connections/${connectionId}/activate-reasoning`, { method: "POST" });
}

export type SlackChannel = { id: string; name: string; is_member: boolean; is_private: boolean };
export function listSlackChannels(connectionId: string): Promise<SlackChannel[]> {
  return jsonRequest(`/v1/integrations/connections/${connectionId}/slack-channels`);
}

export type CloudTalkAgent = { id: string; name: string };
export function listCloudTalkAgents(connectionId: string): Promise<{
  agents: CloudTalkAgent[];
  selected_agent_id: string | null;
}> {
  return jsonRequest(`/v1/integrations/connections/${connectionId}/cloudtalk-agents`);
}
export function selectCloudTalkAgent(connectionId: string, agentId: string): Promise<ProviderConnection> {
  return jsonRequest(`/v1/integrations/connections/${connectionId}/cloudtalk-agent`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ agent_id: agentId }),
  });
}

export type SlackMember = { id: string; name: string };
export function listSlackChannelMembers(connectionId: string, channelId: string): Promise<SlackMember[]> {
  return jsonRequest(`/v1/integrations/connections/${connectionId}/slack-channels/${encodeURIComponent(channelId)}/members`);
}
export function selectSlackChannel(connectionId: string, channelId: string): Promise<SlackChannel> {
  return jsonRequest(`/v1/integrations/connections/${connectionId}/slack-channel`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ channel_id: channelId }),
  });
}
export function sendSlackTestNotification(connectionId: string): Promise<{ sent: boolean; message: string }> {
  return jsonRequest(`/v1/integrations/connections/${connectionId}/slack-test-notification`, { method: "POST" });
}

export function getSlackNotificationConfig(): Promise<SlackNotificationConfig> {
  return jsonRequest("/v1/integrations/slack/notification-config");
}

export function updateSlackNotificationConfig(config: {
  default_connection_id: string | null;
  rules: Array<{
    id?: string;
    name: string;
    connection_id: string;
    destination_type: "channel" | "direct_message";
    channel_id: string | null;
    queues: string[];
    qualification_bands: string[];
    sources: string[];
    weight: number;
  }>;
}): Promise<SlackNotificationConfig> {
  return jsonRequest("/v1/integrations/slack/notification-config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
}

export function disconnectIntegration(connectionId: string): Promise<ProviderConnection> {
  return jsonRequest(`/v1/integrations/connections/${connectionId}/disconnect`, { method: "POST" });
}

export function reconnectIntegration(connectionId: string, credentials: Record<string, string>): Promise<ProviderConnection> {
  return jsonRequest(`/v1/integrations/connections/${connectionId}/reconnect`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ credentials }),
  });
}

export function updateOpenAIModel(connectionId: string, model: string): Promise<ProviderConnection> {
  return jsonRequest<ProviderConnection>(`/v1/integrations/connections/${connectionId}/openai-model`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model }),
  });
}

export function updateReasoningModel(connectionId: string, model: string): Promise<ProviderConnection> {
  return jsonRequest<ProviderConnection>(`/v1/integrations/connections/${connectionId}/reasoning-model`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model }),
  });
}

export function getLead(leadId: string, signal?: AbortSignal): Promise<Lead> {
  return jsonRequest<Lead>(`/v1/leads/${leadId}`, { signal });
}

export function getLeadPipelineHistory(leadId: string, signal?: AbortSignal): Promise<LeadPipelineEvent[]> {
  return jsonRequest<LeadPipelineEvent[]>(`/v1/leads/${leadId}/pipeline-history`, { signal });
}

export function listCalendarEvents(start: string, end: string, signal?: AbortSignal): Promise<WorkspaceCalendarEvent[]> {
  const query = new URLSearchParams({ start, end });
  return jsonRequest<WorkspaceCalendarEvent[]>(`/v1/calendar/events?${query}`, { signal });
}

export function createCalendarEvent(input: {
  kind: WorkspaceCalendarEvent["kind"];
  title: string;
  start_at: string;
  end_at: string;
  location?: string | null;
  notes?: string | null;
  lead_id?: string | null;
}): Promise<WorkspaceCalendarEvent> {
  return jsonRequest<WorkspaceCalendarEvent>("/v1/calendar/events", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
  });
}

export function deleteCalendarEvent(id: string): Promise<void> {
  if (!id.startsWith("manual:")) throw new Error("Only manual events can be deleted here.");
  return jsonRequest<void>(`/v1/calendar/events/${encodeURIComponent(id.slice(7))}`, { method: "DELETE" });
}

export function getLeadFollowUpHistory(leadId: string, signal?: AbortSignal): Promise<FollowUpEmail[]> {
  return jsonRequest<FollowUpEmail[]>(`/v1/leads/${leadId}/follow-up/history`, { signal });
}

export function getReasoningUsage(period: ReasoningUsagePeriod, signal?: AbortSignal): Promise<ReasoningUsage> {
  return jsonRequest<ReasoningUsage>(`/v1/integrations/reasoning/usage?period=${period}`, { signal });
}
export function getLeadMomentum(leadId: string, signal?: AbortSignal): Promise<import("./types").LeadMomentum> {
  return jsonRequest<import("./types").LeadMomentum>(`/v1/leads/${leadId}/momentum`, { signal });
}

export function updateLeadProgress(leadId: string, input: {
  stage: string; note: string; conversion_type?: "project" | "sale";
  closed_revenue?: number | null; credited_user_id?: string | null; send_direct_message?: boolean;
}): Promise<LeadProgressResult & { outcome?: import("./types").LeadOutcome | null }> {
  return jsonRequest<LeadProgressResult>(`/v1/leads/${leadId}/progress`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
  });
}

export function getLeadClosingOptions(signal?: AbortSignal): Promise<Array<{ user_id: string; display_name: string; slack_linked: boolean }>> {
  return jsonRequest("/v1/leads/closing-options", { signal });
}

export function retryLeadCongratulations(leadId: string): Promise<import("./types").LeadOutcome> {
  return jsonRequest(`/v1/leads/${leadId}/congratulations/retry`, { method: "POST" });
}

export function updateMemberSlackLink(memberId: string, slackMemberId: string | null): Promise<WorkspaceMember> {
  return jsonRequest(`/v1/members/${memberId}/slack-link`, {
    method: "PATCH", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ slack_member_id: slackMemberId }),
  });
}

export function syncLeadGmailConversation(leadId: string): Promise<GmailConversationSyncResult> {
  return jsonRequest<GmailConversationSyncResult>(`/v1/leads/${leadId}/gmail-conversation/sync`, { method: "POST" });
}

export function retryConfirmedMeetingBooking(
  leadId: string,
  offerSourceMessageId: string,
): Promise<RetryConfirmedMeetingBookingResult> {
  return jsonRequest<RetryConfirmedMeetingBookingResult>(`/v1/leads/${leadId}/meeting-booking/retry`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ offer_source_message_id: offerSourceMessageId }),
  });
}

export function getLeadMeetingRecommendations(leadId: string, signal?: AbortSignal): Promise<MeetingRecommendations> {
  return jsonRequest<MeetingRecommendations>(`/v1/leads/${leadId}/meeting-recommendations`, { signal });
}

export function createLeadMeetingOfferDraft(
  leadId: string,
  slot: MeetingSuggestion,
): Promise<import("./types").MeetingOfferDraftResult> {
  return jsonRequest<import("./types").MeetingOfferDraftResult>(`/v1/leads/${leadId}/meetings/offer-draft`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      provider: slot.provider,
      connection_id: slot.connection_id,
      source_message_id: slot.source_message_id,
      source_gmail_connection_id: slot.source_gmail_connection_id,
      start_at: slot.start_at,
      end_at: slot.end_at,
      time_zone: slot.time_zone,
      event_type_uri: slot.event_type_uri,
      event_type_name: slot.event_type_name,
    }),
  });
}

export function createLeadMeetingBookingLinkDraft(
  leadId: string,
): Promise<import("./types").MeetingOfferDraftResult> {
  return jsonRequest<import("./types").MeetingOfferDraftResult>(
    `/v1/leads/${leadId}/meetings/booking-link-draft`,
    { method: "POST" },
  );
}

export function confirmLeadMeeting(leadId: string, slot: MeetingSuggestion): Promise<ConfirmMeetingResult> {
  return jsonRequest<ConfirmMeetingResult>(`/v1/leads/${leadId}/meetings/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      provider: slot.provider,
      connection_id: slot.connection_id,
      source_message_id: slot.source_message_id,
      source_gmail_connection_id: slot.source_gmail_connection_id,
      start_at: slot.start_at,
      end_at: slot.end_at,
      time_zone: slot.time_zone,
      event_type_uri: slot.event_type_uri,
      event_type_name: slot.event_type_name,
    }),
  });
}

export function getLeadFollowUp(leadId: string, signal?: AbortSignal): Promise<FollowUpEmail | null> {
  return jsonRequest<FollowUpEmail | null>(`/v1/leads/${leadId}/follow-up`, { signal });
}

export function generateLeadFollowUp(leadId: string): Promise<FollowUpEmail> {
  return jsonRequest<FollowUpEmail>(`/v1/leads/${leadId}/follow-up/draft`, { method: "POST" });
}

export function updateLeadFollowUp(leadId: string, draftId: string, input: { subject: string; body: string }): Promise<FollowUpEmail> {
  return jsonRequest<FollowUpEmail>(`/v1/leads/${leadId}/follow-up/${draftId}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
  });
}

export function approveAndSendLeadFollowUp(leadId: string, draftId: string): Promise<FollowUpEmail> {
  return jsonRequest<FollowUpEmail>(`/v1/leads/${leadId}/follow-up/${draftId}/send`, { method: "POST" });
}

function analyticsRequest<T>(path: string, range: TimeRange, signal?: AbortSignal): Promise<T> {
  return jsonRequest<T>(`/v1/analytics/${path}?range=${range}`, { signal });
}

export const getDashboard = (range: TimeRange, signal?: AbortSignal) => analyticsRequest<DashboardAnalytics>("dashboard", range, signal);
export const getChannelAnalytics = (range: TimeRange, signal?: AbortSignal) => analyticsRequest<ChannelAnalytics>("channels", range, signal);
export const getPipelineAnalytics = (range: TimeRange, signal?: AbortSignal) => analyticsRequest<PipelineAnalytics>("pipeline", range, signal);
export const getRevenueAnalytics = (range: TimeRange, signal?: AbortSignal) => analyticsRequest<RevenueAnalytics>("revenue", range, signal);
export const getRepAnalytics = (range: TimeRange, signal?: AbortSignal) => analyticsRequest<RepAnalytics>("reps", range, signal);
export const getTimeSeriesAnalytics = (range: TimeRange, signal?: AbortSignal) => analyticsRequest<TimeSeriesAnalytics>("time-series", range, signal);
export const getAgentActivity = (range: TimeRange, signal?: AbortSignal) => analyticsRequest<AgentActivity>("agent-activity", range, signal);
export const getManualReview = (range: TimeRange, signal?: AbortSignal) => analyticsRequest<ManualReviewAnalytics>("manual-review", range, signal);
export const getExtendedAnalytics = (range: TimeRange, signal?: AbortSignal) => analyticsRequest<ExtendedAnalytics>("extended", range, signal);
export const getAdvancedAnalytics = (months = 6, signal?: AbortSignal) =>
  jsonRequest<AdvancedAnalytics>(`/v1/intelligence/analytics?months=${months}`, { signal });

export const listAssignmentNotifications = (signal?: AbortSignal) =>
  jsonRequest<AssignmentNotification[]>("/v1/notifications/assignments", { signal });

export const markAssignmentNotificationRead = (id: string) =>
  jsonRequest<AssignmentNotification>(
    `/v1/notifications/assignments/${encodeURIComponent(id)}/read`,
    { method: "PATCH" },
  );

export function listMembers(signal?: AbortSignal): Promise<WorkspaceMember[]> {
  return jsonRequest<WorkspaceMember[]>("/v1/members", { signal });
}

export function listInvitations(signal?: AbortSignal): Promise<WorkspaceInvitation[]> {
  return jsonRequest<WorkspaceInvitation[]>("/v1/invitations", { signal });
}

export function listMemberAuditEvents(signal?: AbortSignal): Promise<WorkspaceAuditEvent[]> {
  return jsonRequest<WorkspaceAuditEvent[]>("/v1/members/audit-events", { signal });
}

export function inviteMember(input: {
  email: string;
  role: string;
  permission_overrides?: PermissionOverrides;
}): Promise<WorkspaceInvitation> {
  return jsonRequest<WorkspaceInvitation>("/v1/members/invite", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email: input.email,
      role: input.role,
      grants: input.permission_overrides?.grants ?? [],
      denies: input.permission_overrides?.denies ?? [],
    }),
  });
}

export function resendInvitation(invitationId: string): Promise<WorkspaceInvitation> {
  return jsonRequest<WorkspaceInvitation>(`/v1/invitations/${invitationId}/resend`, { method: "POST" });
}

export function revokeInvitation(invitationId: string): Promise<WorkspaceInvitation> {
  return jsonRequest<WorkspaceInvitation>(`/v1/invitations/${invitationId}/revoke`, { method: "POST" });
}

export function updateMemberRole(memberId: string, role: string): Promise<WorkspaceMember> {
  return jsonRequest<WorkspaceMember>(`/v1/members/${memberId}/role`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role }),
  });
}

export function updateMemberPermissions(memberId: string, overrides: PermissionOverrides): Promise<WorkspaceMember> {
  return jsonRequest<WorkspaceMember>(`/v1/members/${memberId}/permissions`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(overrides),
  });
}

export const getCurrentIcp = (signal?: AbortSignal) =>
  jsonRequest<IcpVersion>("/v1/icp/current", { signal });

export const getSystemDefaultIcp = (signal?: AbortSignal) =>
  jsonRequest<IcpVersion>("/v1/icp/default", { signal });

export const listIcpVersions = (signal?: AbortSignal) =>
  jsonRequest<IcpVersion[]>("/v1/icp/versions", { signal });

export function createIcpDraft(input: {
  name?: string;
  description?: string;
  based_on_version_id?: string;
} = {}): Promise<IcpVersion> {
  return jsonRequest<IcpVersion>("/v1/icp/drafts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export function updateIcpDraft(id: string, input: {
  name?: string;
  description?: string;
  configuration?: IcpConfiguration;
}): Promise<IcpVersion> {
  return jsonRequest<IcpVersion>(`/v1/icp/versions/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export const validateIcpDraft = (id: string) =>
  jsonRequest<IcpValidation>(`/v1/icp/versions/${id}/validate`, { method: "POST" });

export const previewIcpDraft = (id: string, limit = 100) =>
  jsonRequest<IcpPreview>(`/v1/icp/versions/${id}/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ limit }),
  });

export const publishIcpDraft = (id: string, confirmExclusions: boolean) =>
  jsonRequest<IcpVersion>(`/v1/icp/versions/${id}/publish`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      confirm_new_leads_use_version: true,
      confirm_historical_results_preserved: true,
      confirm_exclusion_changes: confirmExclusions,
    }),
  });

export const archiveIcpVersion = (id: string) =>
  jsonRequest<IcpVersion>(`/v1/icp/versions/${id}/archive`, { method: "POST" });

export function suspendMember(memberId: string): Promise<WorkspaceMember> {
  return jsonRequest<WorkspaceMember>(`/v1/members/${memberId}/suspend`, { method: "POST" });
}

export function reactivateMember(memberId: string): Promise<WorkspaceMember> {
  return jsonRequest<WorkspaceMember>(`/v1/members/${memberId}/reactivate`, { method: "POST" });
}

export function removeMember(memberId: string): Promise<WorkspaceMember> {
  return jsonRequest<WorkspaceMember>(`/v1/members/${memberId}`, { method: "DELETE" });
}
