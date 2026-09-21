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
} from "./types";

export const DEMO_WORKSPACE_ID = "00000000-0000-0000-0000-000000000006";
export const ACTIVE_WORKSPACE_ID = import.meta.env.VITE_WORKSPACE_ID || DEMO_WORKSPACE_ID;

function withWorkspace(headers?: HeadersInit): Headers {
  const result = new Headers(headers);
  result.set("X-Workspace-ID", ACTIVE_WORKSPACE_ID);
  return result;
}

export async function createLead(payload: LeadPayload): Promise<Lead> {
  const response = await fetch("/v1/leads", {
    method: "POST",
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
  const response = await fetch("/v1/leads", { headers: withWorkspace() });
  if (!response.ok) throw new Error("Unable to load leads");
  const body = (await response.json()) as { items: Lead[] };
  return body.items;
}

export async function getCrmSyncHistory(leadId: string): Promise<CrmSync[]> {
  const response = await fetch(`/v1/leads/${leadId}/crm-sync/history`, {
    headers: withWorkspace(),
  });
  if (!response.ok) throw new Error("Unable to load CRM synchronization history");
  return response.json() as Promise<CrmSync[]>;
}

async function jsonRequest<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, { ...options, headers: withWorkspace(options?.headers) });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: "Integration request failed" }));
    throw new Error(body.detail ?? "Integration request failed");
  }
  return response.json() as Promise<T>;
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
