import { request } from "./api";
export type Company = {
  id: string;
  name: string;
  domain: string | null;
  website: string | null;
  website_reviewed: boolean;
  industry: string | null;
  profile: Record<string, unknown>;
};
export type Contact = {
  id: string;
  name: string;
  email: string | null;
  company_id: string | null;
  phone: string | null;
  job_title: string | null;
  timezone: string;
  updated_at: string;
};
export type Territory = {
  id: string;
  name: string;
  member_ids: string[];
  regions: string[];
  industries: string[];
  products: string[];
  sla_hours: number;
};
export type Capacity = {
  user_id: string;
  max_open_leads: number;
  available: boolean;
  available_until: string | null;
};
export type Policy = {
  territories: Territory[];
  capacity: Capacity[];
  channel_costs: { source: string; month: string; cost_usd: number }[];
  battlecards: {
    competitor: string;
    response: string;
    evidence_url: string | null;
  }[];
  require_recorded_consent: boolean;
  retention_days: number | null;
  required_region: string;
  watcher_enabled: boolean;
  researcher_enabled: boolean;
  coach_digest_enabled: boolean;
  approval_tiers: Record<string, string>;
  brand_name: string | null;
  brand_logo_url: string | null;
  brand_accent: string;
  custom_domain: string | null;
  monthly_quota_usd: number | null;
};
export type PolicyResponse = {
  configuration: Policy;
  version: number;
  hosting_region: string;
  members: { user_id: string; name: string; open_leads: number }[];
};
export type Evidence = {
  field: string;
  value: string;
  excerpt: string;
  source: string;
};
export type InsightContent = {
  summary: string;
  recommendations?: string[];
  facts?: Evidence[];
  sources?: { url: string; fetched_at: string }[];
  limitations?: string;
  deltas?: { component: string; criterion: string; from: number; to: number }[];
  wins?: number;
  losses?: number;
  sample_size?: number;
  draft_id?: string;
  recipient_id?: string;
  objections?: {
    tag: string;
    excerpt: string;
    source: string;
    outcome: string;
  }[];
  battlecards?: {
    competitor: string;
    response: string;
    evidence_url: string | null;
  }[];
  evidence?: { text: string; source: string; direction: string }[];
  scorecard?: {
    recorded_outbound_emails: number;
    questions: number;
    questions_per_100_words: number | null;
  };
  suggestions?: {
    recommendation: string;
    lead_id: string;
    evidence: { text: string; source: string }[];
  }[];
  score?: number;
  previous_score?: number;
  score_delta?: number;
};
export type Insight = {
  id: string;
  kind: string;
  lead_id: string | null;
  company_id: string | null;
  status: string;
  content: InsightContent;
  created_at: string;
};
export type NodeType =
  | "start"
  | "wait"
  | "condition"
  | "draft"
  | "research"
  | "coach"
  | "note"
  | "end";
export type PlaybookNode = {
  id: string;
  type: NodeType;
  x: number;
  y: number;
  next: string | null;
  otherwise: string | null;
  wait_hours: number;
  condition: string;
  text: string;
};
export type AutomationSpec = {
  name: string;
  kind: "sequence" | "playbook";
  active: boolean;
  nodes: PlaybookNode[];
  timezone: string;
  preferred_hour: number;
  optimize_time: boolean;
  version: number;
};
export type Automation = {
  id: string;
  name: string;
  kind: "sequence" | "playbook";
  active: boolean;
  version: number;
  specification: AutomationSpec;
};
export type Run = {
  id: string;
  lead_id: string;
  definition_id: string;
  cursor: string | null;
  status: string;
  due_at: string;
  last_error: string | null;
  state: {
    draft_id?: string;
    timing?: { timezone: string; hour: number; method: string };
  };
};
export type Consent = {
  id: string;
  email: string;
  channel: string;
  status: string;
  basis: string;
  purpose: string;
  evidence: string;
  created_at: string;
};
export type Note = {
  id: string;
  body: string;
  version: number;
  author_id: string;
  updated_by: string;
  updated_at: string;
};
export type Collaboration = {
  notes: Note[];
  presence: { user_id: string; name: string }[];
};
export type AdvancedAnalytics = {
  summary: string;
  summary_method: string;
  fairness_method: string;
  sample_size: number;
  truncated: boolean;
  forecast: {
    closed_usd_this_month: number;
    open_pipeline_usd: number;
    historical_close_rate: number | null;
    expected_open_pipeline_usd: number | null;
    quota_usd: number | null;
    coverage: number | null;
    closed_sample_size: number;
    method: string;
    unvalued_open_leads: number;
    scope: string;
  };
  channels: {
    source: string;
    month: string;
    leads: number;
    qualified: number;
    won: number;
    lost: number;
    cost_usd: number | null;
    closed_usd: number;
    cost_per_qualified: number | null;
    roi_percent: number | null;
    method: string;
  }[];
  cohorts: {
    week: string;
    campaign: string;
    leads: number;
    qualified: number;
    won: number;
    lost: number;
  }[];
  members: {
    user_id: string;
    name: string;
    closed: number;
    wins: number;
    comparable_leads: number;
    expected_wins: number;
    adjusted_win_index: number | null;
  }[];
};
export const opsGet = <T>(path: string, signal?: AbortSignal) =>
  request<T>(path, { signal });
export const opsSave = <T>(
  path: string,
  data: unknown,
  method = "POST",
  feedback?: { activityToast?: boolean },
) =>
  request<T>(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  }, feedback);
