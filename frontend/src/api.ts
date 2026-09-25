import type {
  AgentActivity,
  ChannelAnalytics,
  CrmSync,
  CrmInboundSyncRun,
  CrmInboundSyncState,
  DashboardAnalytics,
  IntegrationCard,
  Lead,
  LeadPayload,
  ManualReviewAnalytics,
  PipelineAnalytics,
  ProviderConnection,
  RepAnalytics,
  RevenueAnalytics,
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
} from "./types";

export const DEMO_WORKSPACE_ID = "00000000-0000-0000-0000-000000000006";
const CSRF_COOKIE_NAME = import.meta.env.VITE_AUTH_CSRF_COOKIE_NAME || "lead_agent_csrf";
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");
let activeWorkspaceId = import.meta.env.VITE_WORKSPACE_ID || DEMO_WORKSPACE_ID;
const inFlightGets = new Map<string, Promise<unknown>>();

export function setActiveWorkspace(workspaceId: string) {
  activeWorkspaceId = workspaceId;
}

export function getActiveWorkspace() {
  return activeWorkspaceId;
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
    throw new Error(typeof body.detail === "string" ? body.detail : "Request failed");
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const getSession = () => request<AuthSession | null>("/v1/auth/status");

export async function signIn(email: string, password: string): Promise<AuthSession> {
  const session = await request<AuthSession>("/v1/auth/signin", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  setActiveWorkspace(session.current_workspace_id);
  return session;
}

export async function signUp(input: {
  email: string;
  password: string;
  display_name: string;
  workspace_name: string;
}): Promise<AuthSession> {
  const session = await request<AuthSession>("/v1/auth/signup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  setActiveWorkspace(session.current_workspace_id);
  return session;
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

export async function listLeads(signal?: AbortSignal): Promise<Lead[]> {
  const body = await request<{ items: Lead[] }>("/v1/leads", { signal });
  return body.items;
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
  const filename = disposition.match(/filename="([^"]+)"/)?.[1] || `ingenuity-leads-${new Date().toISOString().slice(0, 10)}.${input.format}`;
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
  const objectType = provider === "hubspot" ? "contacts" : "Lead";
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
    body: JSON.stringify({ enabled, object_type: provider === "hubspot" ? "contacts" : "Lead", interval_seconds: intervalSeconds }),
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
    body: JSON.stringify({ mode, object_type: provider === "hubspot" ? "contacts" : "Lead", limit: 500, page_size: 100 }),
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

export function bulkSendLeadsToCrm(leadIds: string[]): Promise<{
  requested: number;
  succeeded: number;
  failed: number;
}> {
  return jsonRequest("/v1/leads/bulk-crm-sync", {
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

export function addIntegrationCredential(provider: string, accountName: string, authType: string, scopes: string[], credentials: Record<string, string>): Promise<ProviderConnection> {
  return jsonRequest(`/v1/integrations/${provider}/credentials`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ account_name: accountName, auth_type: authType, scopes, credentials, metadata: {}, activate: true }),
  });
}

export function testIntegration(connectionId: string): Promise<{ connection: ProviderConnection; valid: boolean; verified: boolean; status: string; message: string; retryable: boolean; error_code: string | null }> {
  return jsonRequest(`/v1/integrations/connections/${connectionId}/test`, { method: "POST" });
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

export function getLead(leadId: string, signal?: AbortSignal): Promise<Lead> {
  return jsonRequest<Lead>(`/v1/leads/${leadId}`, { signal });
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
