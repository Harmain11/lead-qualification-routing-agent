import type {
  AgentActivity,
  ChannelAnalytics,
  CrmSync,
  CrmInboundSyncRun,
  CrmInboundSyncState,
  DashboardAnalytics,
  IntegrationCard,
  Lead,
  LeadPipelineEvent,
  LeadProgressResult,
  GmailConversationSyncResult,
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
} from "./types";

export const DEMO_WORKSPACE_ID = "00000000-0000-0000-0000-000000000006";
const CSRF_COOKIE_NAME = import.meta.env.VITE_AUTH_CSRF_COOKIE_NAME || "lead_agent_csrf";
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");
let activeWorkspaceId = import.meta.env.VITE_WORKSPACE_ID || DEMO_WORKSPACE_ID;
const inFlightGets = new Map<string, Promise<unknown>>();

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "ApiError";
  }
}

export function setActiveWorkspace(workspaceId: string) {
  activeWorkspaceId = workspaceId;
}

export function getActiveWorkspace() {
  return activeWorkspaceId;
}

export function getRoutingConfig(): Promise<RoutingConfig> {
  return request<RoutingConfig>("/v1/routing/config");
}

export function updateRoutingConfig(member_percentages: Record<string, number>): Promise<RoutingConfig> {
  return request<RoutingConfig>("/v1/routing/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ member_percentages }),
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
  const value = document.cookie
    .split("; ")
    .find((item) => item.startsWith(`${CSRF_COOKIE_NAME}=`))
    ?.split("=", 2)[1];
  return value ? decodeURIComponent(value) : null;
}

function withWorkspace(headers?: HeadersInit): Headers {
  const result = new Headers(headers);
  result.set("X-Workspace-ID", activeWorkspaceId);
  const csrf = csrfToken();
  if (csrf) result.set("X-CSRF-Token", csrf);
  return result;
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const method = options?.method?.toUpperCase() ?? "GET";
  const key = method === "GET" && !options?.signal ? `${activeWorkspaceId}:${url}` : null;
  if (key) {
    const existing = inFlightGets.get(key);
    if (existing) return existing as Promise<T>;
  }

  const pending = performRequest<T>(url, options);
  if (!key) return pending;
  inFlightGets.set(key, pending);
  try {
    return await pending;
  } finally {
    if (inFlightGets.get(key) === pending) inFlightGets.delete(key);
  }
}

async function performRequest<T>(url: string, options?: RequestInit): Promise<T> {
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
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const getSession = () => request<AuthSession | null>("/v1/auth/status");

export const getEmailTemplates = () => request<WorkspaceEmailTemplate[]>("/v1/settings/email-templates");
export const saveEmailTemplate = (kind: WorkspaceEmailTemplate["kind"], template: Omit<WorkspaceEmailTemplate, "kind">) =>
  request<WorkspaceEmailTemplate>(`/v1/settings/email-templates/${kind}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(template),
  });
export const generateEmailTemplate = (kind: WorkspaceEmailTemplate["kind"]) =>
  request<WorkspaceEmailTemplate>(`/v1/settings/email-templates/${kind}/generate`, { method: "POST" });

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

export async function createLead(payload: LeadPayload): Promise<Lead> {
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
  return { blob: await response.blob(), filename };
}

export function getCrmSyncHistory(leadId: string, signal?: AbortSignal): Promise<CrmSync[]> {
  return request(`/v1/leads/${leadId}/crm-sync/history`, { signal });
}

async function jsonRequest<T>(url: string, options?: RequestInit): Promise<T> {
  return request<T>(url, options);
}

export function listIntegrations(): Promise<IntegrationCard[]> {
  return jsonRequest<IntegrationCard[]>("/v1/integrations");
}

export function getInboundSyncState(connectionId: string, provider: string = "salesforce"): Promise<CrmInboundSyncState> {
  const objectType = provider === "hubspot" ? "contacts" : ["gmail", "outlook"].includes(provider) ? "message" : "Lead";
  return jsonRequest(`/v1/integrations/connections/${connectionId}/inbound-sync/state?object_type=${objectType}`);
}

export function configureInboundSync(
  connectionId: string,
  enabled: boolean,
  provider: string = "salesforce",
  intervalSeconds = 300,
): Promise<CrmInboundSyncState> {
  return jsonRequest(`/v1/integrations/connections/${connectionId}/inbound-sync/configuration`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled, object_type: provider === "hubspot" ? "contacts" : ["gmail", "outlook"].includes(provider) ? "message" : "Lead", interval_seconds: intervalSeconds }),
  });
}

export function runInboundSync(
  connectionId: string,
  mode: "initial" | "incremental" | "manual",
  provider: string = "salesforce",
): Promise<CrmInboundSyncRun> {
  return jsonRequest(`/v1/integrations/connections/${connectionId}/inbound-sync`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode, object_type: provider === "hubspot" ? "contacts" : ["gmail", "outlook"].includes(provider) ? "message" : "Lead", limit: ["gmail", "outlook"].includes(provider) ? 100 : 500, page_size: 100, send_confirmation_email: false }),
  });
}

export function sendLeadToCrm(leadId: string, provider?: string): Promise<CrmSync> {
  const query = provider ? `?provider=${encodeURIComponent(provider)}` : "";
  return jsonRequest(`/v1/leads/${leadId}/crm-sync${query}`, { method: "POST" });
}

export function updateLeadTitle(leadId: string, jobTitle: string): Promise<Lead> {
  return jsonRequest(`/v1/leads/${leadId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_title: jobTitle }),
  });
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

export function testIntegration(connectionId: string): Promise<{ connection: ProviderConnection; valid: boolean; verified: boolean; status: string; message: string; retryable: boolean; error_code: string | null }> {
  return jsonRequest(`/v1/integrations/connections/${connectionId}/test`, { method: "POST" });
}

export function backfillOpenAI(connectionId: string): Promise<{ status: string; queued_count: number; message: string }> {
  return jsonRequest(`/v1/integrations/connections/${connectionId}/backfill-openai`, { method: "POST" });
}

export type SlackChannel = { id: string; name: string };
export function listSlackChannels(connectionId: string): Promise<SlackChannel[]> {
  return jsonRequest(`/v1/integrations/connections/${connectionId}/slack-channels`);
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

export function getLead(leadId: string, signal?: AbortSignal): Promise<Lead> {
  return jsonRequest<Lead>(`/v1/leads/${leadId}`, { signal });
}

export function getLeadPipelineHistory(leadId: string, signal?: AbortSignal): Promise<LeadPipelineEvent[]> {
  return jsonRequest<LeadPipelineEvent[]>(`/v1/leads/${leadId}/pipeline-history`, { signal });
}

export function getLeadMomentum(leadId: string, signal?: AbortSignal): Promise<import("./types").LeadMomentum> {
  return jsonRequest<import("./types").LeadMomentum>(`/v1/leads/${leadId}/momentum`, { signal });
}

export function updateLeadProgress(leadId: string, input: { stage: string; note: string }): Promise<LeadProgressResult> {
  return jsonRequest<LeadProgressResult>(`/v1/leads/${leadId}/progress`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
  });
}

export function syncLeadGmailConversation(leadId: string): Promise<GmailConversationSyncResult> {
  return jsonRequest<GmailConversationSyncResult>(`/v1/leads/${leadId}/gmail-conversation/sync`, { method: "POST" });
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
