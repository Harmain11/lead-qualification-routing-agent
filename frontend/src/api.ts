import type {
  AgentActivity,
  ChannelAnalytics,
  CrmSync,
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
} from "./types";

export const DEMO_WORKSPACE_ID = "00000000-0000-0000-0000-000000000006";
const CSRF_COOKIE_NAME = import.meta.env.VITE_AUTH_CSRF_COOKIE_NAME || "lead_agent_csrf";
let activeWorkspaceId = import.meta.env.VITE_WORKSPACE_ID || DEMO_WORKSPACE_ID;

export function setActiveWorkspace(workspaceId: string) {
  activeWorkspaceId = workspaceId;
}

export function getActiveWorkspace() {
  return activeWorkspaceId;
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
  const response = await fetch(url, {
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
  const response = await fetch("/v1/leads", {
    method: "POST",
    credentials: "include",
    headers: withWorkspace({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: "Unable to create lead" }));
    throw new Error(error.detail ?? "Unable to create lead");
  }
  return response.json() as Promise<Lead>;
}

export async function listLeads(): Promise<Lead[]> {
  const response = await fetch("/v1/leads", { credentials: "include", headers: withWorkspace() });
  if (!response.ok) throw new Error("Unable to load leads");
  const body = (await response.json()) as { items: Lead[] };
  return body.items;
}

export async function getCrmSyncHistory(leadId: string): Promise<CrmSync[]> {
  const response = await fetch(`/v1/leads/${leadId}/crm-sync/history`, {
    headers: withWorkspace(),
    credentials: "include",
  });
  if (!response.ok) throw new Error("Unable to load CRM synchronization history");
  return response.json() as Promise<CrmSync[]>;
}

async function jsonRequest<T>(url: string, options?: RequestInit): Promise<T> {
  return request<T>(url, options);
}

export function listIntegrations(): Promise<IntegrationCard[]> {
  return jsonRequest<IntegrationCard[]>("/v1/integrations");
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

export function testIntegration(connectionId: string): Promise<{ connection: ProviderConnection; valid: boolean; message: string }> {
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

export function getLead(leadId: string): Promise<Lead> {
  return jsonRequest<Lead>(`/v1/leads/${leadId}`);
}

function analyticsRequest<T>(path: string, range: TimeRange): Promise<T> {
  return jsonRequest<T>(`/v1/analytics/${path}?range=${range}`);
}

export const getDashboard = (range: TimeRange) =>
  analyticsRequest<DashboardAnalytics>("dashboard", range);
export const getChannelAnalytics = (range: TimeRange) =>
  analyticsRequest<ChannelAnalytics>("channels", range);
export const getPipelineAnalytics = (range: TimeRange) =>
  analyticsRequest<PipelineAnalytics>("pipeline", range);
export const getRevenueAnalytics = (range: TimeRange) =>
  analyticsRequest<RevenueAnalytics>("revenue", range);
export const getRepAnalytics = (range: TimeRange) =>
  analyticsRequest<RepAnalytics>("reps", range);
export const getTimeSeriesAnalytics = (range: TimeRange) =>
  analyticsRequest<TimeSeriesAnalytics>("time-series", range);
export const getAgentActivity = (range: TimeRange) =>
  analyticsRequest<AgentActivity>("agent-activity", range);
export const getManualReview = (range: TimeRange) =>
  analyticsRequest<ManualReviewAnalytics>("manual-review", range);
