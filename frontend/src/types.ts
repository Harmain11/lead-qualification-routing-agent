export type ScoreLine = {
  component: string;
  key: string;
  points: number;
  max_points: number;
  reason: string;
  evidence: string[];
};

export type Qualification = {
  id: string | null;
  total_score: number;
  classification: string;
  routing_queue: string;
  routing_action: string;
  hard_disqualified: boolean;
  disqualifier_code: string | null;
  disqualifier_evidence: string[];
  rationale: string[];
  score_breakdown: ScoreLine[];
  component_scores: Record<string, number>;
  policy_version: string;
  icp_version_id: string | null;
  icp_source: "system_default" | "workspace" | string;
  icp_profile_version: string;
  icp_configuration_hash: string;
  confidence: number;
  created_at: string | null;
};

export type IcpConfiguration = {
  schema_version: number;
  profile_name: string;
  profile_version: string;
  target_industries: string[];
  excluded_industries: string[];
  target_geographies: string[];
  target_business_types: string[];
  strategic_accounts: string[];
  company_size: {
    employees_min: number;
    employees_max: number;
    revenue_currency: string;
    annual_revenue_min: number;
    annual_revenue_max: number;
  };
  buyer_roles: Record<string, string[]>;
  buyer: {
    job_titles: string[];
    seniorities: string[];
    departments: string[];
    decision_maker_indicators: string[];
    excluded_personas: string[];
  };
  budget: { currency: string; minimum: number; typical: number };
  commercial: { service_interests: string[]; purchasing_timelines: string[] };
  technology: { technology_stack: string[]; platform_requirements: string[] };
  fit_signals: { required: string[]; preferred: string[]; exclusions: string[] };
  pain_points: string[];
  disqualifiers: Array<{
    code: string;
    description: string;
    severity: "hard";
    field: "searchable" | "industry" | "geography" | "job_title" | "email" | "company_name" | "product_or_service";
    operator: "contains_any" | "matches_any";
    keywords: string[];
    values: string[];
  }>;
  scoring: {
    weights: Record<string, number>;
    component_points: Record<string, Record<string, number>>;
    signals: Record<string, string[]>;
    message_detail_chars: number;
  };
};

export type IcpVersion = {
  id: string | null;
  workspace_id: string;
  source: "system_default" | "workspace";
  version_number: number | null;
  status: "draft" | "published" | "archived";
  name: string;
  description: string;
  configuration: IcpConfiguration;
  weight_total: number;
  created_by: string | null;
  created_at: string | null;
  updated_at: string | null;
  published_by: string | null;
  published_at: string | null;
  effective_at: string | null;
  archived_at: string | null;
  editable: boolean;
};

export type IcpValidation = {
  valid: boolean;
  weight_total: number;
  required_weight_total: number;
  problems: string[];
};

export type IcpPreview = {
  version_id: string;
  leads_evaluated: number;
  changed_count: number;
  transitions: Record<string, number>;
  representative_changes: Array<{
    lead_id: string;
    lead_name: string;
    company_name: string;
    current_score: number;
    proposed_score: number;
    score_delta: number;
    current_classification: string;
    proposed_classification: string;
    important_changed_matches: string[];
  }>;
};

export type EvidenceReference = {
  source_field: string;
  excerpt: string;
};

export type AnalyzedField = {
  value: string | null;
  information_kind: "explicit" | "inferred" | "unknown";
  confidence: number;
  evidence: EvidenceReference[];
};

export type ContextFields = Record<
  | "requested_product_service"
  | "pain_point"
  | "urgency"
  | "explicit_timeline"
  | "budget_information"
  | "company_size_or_spend"
  | "person_role"
  | "decision_maker_status"
  | "buying_stage"
  | "competitor_or_current_tools"
  | "technical_requirements"
  | "previous_engagement"
  | "source_channel_intent",
  AnalyzedField
>;

export type IcpCriterion = {
  status: "match" | "partial" | "no_match" | "unknown" | "disqualified";
  confidence: number;
  rationale: string;
  evidence: EvidenceReference[];
};

export type LeadAnalysis = {
  id: string;
  status: "completed" | "manual_review" | "skipped_hard_disqualifier" | "provider_error";
  provider: string;
  model: string;
  prompt_version: string;
  requires_manual_review: boolean;
  error_code: string | null;
  error_message: string | null;
  context: ContextFields;
  intent_level: "high" | "medium" | "low" | "unknown";
  overall_confidence: number;
  unknown_fields: string[];
  contradictions: string[];
  icp_evaluation: {
    profile_name: string;
    profile_version: string;
    industry_fit: IcpCriterion;
    company_size_fit: IcpCriterion;
    geography_fit: IcpCriterion;
    buyer_role_fit: IcpCriterion;
    budget_fit: IcpCriterion;
    pain_point_relevance: IcpCriterion;
    explicit_disqualifiers: string[];
  };
  summary: {
    who: string;
    company_context: string;
    need: string;
    pain_point: string;
    urgency: string;
    buying_signal: string;
    potential_concern: string;
    qualification_result: string;
  };
  recommended_action: string;
  created_at: string;
};

export type ActionPlan = {
  immediate_action: string;
  response_priority: string;
  talking_points: string[];
  unanswered_questions: string[];
  known_pain_point: string;
  urgency_timeline: string;
  potential_objections: string[];
  next_step_recommendation: string;
  supporting_evidence: string[];
};

export type Notification = {
  id: string;
  provider: string;
  status: "pending" | "sent" | "failed" | "retryable";
  attempts: number;
  external_message_id: string | null;
  error_message: string | null;
  created_at: string;
  sent_at: string | null;
  updated_at: string;
};

export type RoutingDecision = {
  id: string;
  lead_id: string;
  qualification_id: string;
  qualification_band: string;
  assigned_queue: string;
  assigned_rep_id: string | null;
  assigned_rep_name: string | null;
  routing_reason: string;
  matched_rules: string[];
  action_plan: ActionPlan;
  priority: "urgent" | "high" | "normal" | "low" | "none";
  sla_target_at: string | null;
  notification_state: "pending" | "sent" | "failed" | "retryable";
  manual_review: boolean;
  routing_timestamp: string;
  notifications: Notification[];
};

export type CrmSync = {
  id: string;
  workspace_id: string;
  lead_id: string;
  routing_decision_id: string;
  provider_connection_id: string | null;
  provider: "mock" | "salesforce" | "hubspot" | string;
  operation: "create" | "update" | "upsert" | string;
  status: "pending" | "syncing" | "synced" | "failed" | "retryable";
  attempts: number;
  attempt_history: Array<{
    attempt: number;
    status: string;
    operation?: string;
    error_category?: string;
    error_code?: string;
    error?: string;
    completed_at: string;
  }>;
  provider_entity_ids: {
    contact_id: string | null;
    company_id: string | null;
    opportunity_id: string | null;
  };
  payload_version: string;
  payload_hash: string;
  error_category: string | null;
  error_code: string | null;
  last_error: string | null;
  retryable: boolean;
  requested_at: string;
  completed_at: string | null;
  updated_at: string;
  is_current: boolean;
};

export type Lead = {
  id: string;
  workspace_id: string;
  source: string;
  source_detail: string | null;
  external_id: string | null;
  full_name: string;
  email: string;
  phone: string | null;
  job_title: string | null;
  company_name: string;
  industry: string | null;
  employee_count: number | null;
  annual_revenue: number | null;
  geography: string | null;
  budget: number | null;
  product_or_service: string;
  message: string;
  received_at: string;
  normalization_version: string;
  created_at: string;
  qualification: Qualification | null;
  analysis: LeadAnalysis | null;
  routing: RoutingDecision | null;
  crm_sync: CrmSync | null;
};

export type LeadPayload = Omit<
  Lead,
  "id" | "workspace_id" | "normalization_version" | "created_at" | "received_at" | "qualification" | "analysis" | "routing" | "crm_sync" | "external_id" | "source_detail"
> & {
  source: string;
  source_detail: string;
  external_id?: string;
};

export type ProviderConnection = {
  id: string;
  workspace_id: string;
  provider_type: string;
  external_account_id: string | null;
  account_name: string;
  auth_type: string;
  status: "connecting" | "awaiting_authorization" | "validating" | "connected" | "needs_attention" | "authorization_expired" | "connection_failed" | "reconnect_required" | "disconnected";
  scopes: string[];
  credential_state: "configured" | "missing" | "expired" | "invalid";
  last_validation_at: string | null;
  last_validation_result: Record<string, unknown>;
  metadata: Record<string, unknown>;
  is_active: boolean;
  created_at: string;
  updated_at: string;
};

export type AuthSession = {
  user: { id: string; email: string; display_name: string };
  current_workspace_id: string;
  workspaces: Array<{
    id: string;
    name: string;
    role: string;
    is_demo: boolean;
    effective_permissions: string[];
  }>;
  expires_at: string | null;
};

export type PermissionOverrides = {
  grants: string[];
  denies: string[];
};

export type WorkspaceMember = {
  id: string;
  workspace_id: string;
  user_id: string;
  email: string;
  display_name: string;
  role: string;
  role_label: string;
  status: "invited" | "active" | "suspended" | "revoked" | string;
  permission_overrides: PermissionOverrides;
  effective_permissions: string[];
  invited_by: string | null;
  invited_at: string | null;
  accepted_at: string | null;
  updated_at: string;
};

export type WorkspaceInvitation = {
  id: string;
  workspace_id: string;
  email: string;
  role: string;
  role_label: string;
  permission_overrides: PermissionOverrides;
  status: "pending" | "accepted" | "expired" | "revoked" | string;
  invited_by: string;
  accepted_user_id: string | null;
  expires_at: string;
  accepted_at: string | null;
  revoked_at: string | null;
  created_at: string;
  updated_at: string;
};

export type WorkspaceAuditEvent = {
  id: string;
  workspace_id: string;
  actor_id: string | null;
  target_membership_id: string | null;
  operation: string;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  created_at: string;
};

export type IntegrationCard = {
  provider: string;
  display_name: string;
  category: string;
  availability: "available" | "foundation" | "future";
  preferred_auth: string;
  supported_auth: string[];
  dashboard_path: string;
  credential_help: string;
  required_scopes: string[];
  permission_reason: string;
  documentation_url: string;
  manual_fields: string[];
  connection: ProviderConnection | null;
};

export type TimeRange = "weekly" | "monthly" | "annual";

export type AnalyticsMetadata = {
  generated_at: string;
  range: TimeRange;
  range_start: string;
  range_end: string;
  workspace_id: string;
  workspace_name: string;
  data_mode: "demo" | "live";
};

export type LeadCard = {
  id: string;
  name: string;
  company: string;
  source: string;
  service: string;
  score: number;
  band: string;
  received_at: string;
  routing_status: string;
  assigned_rep: string | null;
  crm_status: string;
  manual_review: boolean;
  pipeline_stage: string;
};

export type DashboardAnalytics = {
  metadata: AnalyticsMetadata;
  metrics: Array<{
    key: string;
    label: string;
    value: number;
    change_percent: number | null;
    previous_value: number | null;
  }>;
  band_distribution: Array<{ band: string; count: number }>;
  attention_leads: LeadCard[];
  recent_leads: LeadCard[];
};

export type ChannelAnalytics = {
  metadata: AnalyticsMetadata;
  channels: Array<{
    channel: string;
    lead_volume: number;
    share_percent: number;
    qualification_rate: number;
    sql_conversion_rate: number;
    closed_won_conversion_rate: number;
    revenue: number;
    average_score: number;
    importance_index: number;
  }>;
};

export type PipelineAnalytics = {
  metadata: AnalyticsMetadata;
  stages: Array<{
    stage: string;
    label: string;
    count: number;
    value: number;
    leads: LeadCard[];
  }>;
  bands: Array<{ band: string; count: number }>;
  manual_review: number;
};

export type RevenueAnalytics = {
  metadata: AnalyticsMetadata;
  data_status: "unavailable" | "demo" | "crm" | "mixed";
  currency: string;
  total_revenue: number;
  pipeline_value: number;
  by_channel: Array<{ label: string; value: number }>;
  by_rep: Array<{ label: string; value: number }>;
  by_service: Array<{ label: string; value: number }>;
  over_time: Array<{ period: string; value: number }>;
};

export type RepAnalytics = {
  metadata: AnalyticsMetadata;
  reps: Array<{
    rep_name: string;
    assigned_leads: number;
    qualified_leads: number;
    closed_leads: number;
    conversion_rate: number;
    revenue: number;
    average_response_minutes: number | null;
  }>;
};

export type TimeSeriesAnalytics = {
  metadata: AnalyticsMetadata;
  points: Array<{
    period: string;
    leads: number;
    sqls: number;
    average_score: number;
    revenue: number;
  }>;
};

export type AgentActivity = {
  metadata: AnalyticsMetadata;
  capabilities: Array<{ name: string; status: "implemented" | "future" }>;
  activity: Array<{
    id: string;
    lead_id: string | null;
    agent: string;
    stage: string;
    status: string;
    provider: string | null;
    structured_result: Record<string, unknown>;
    tool_name: string | null;
    duration_ms: number | null;
    error: string | null;
    created_at: string;
  }>;
};

export type ManualReviewAnalytics = {
  metadata: AnalyticsMetadata;
  items: Array<LeadCard & { review_reasons: string[] }>;
  total: number;
};
