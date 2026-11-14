export type CommunicationMember = {
  id: string;
  display_name: string;
  email: string;
};

export type CommunicationLeadReference = {
  id: string;
  full_name: string;
  company_name: string;
  qualification: string | null;
  source: string;
};

export type CommunicationProject = {
  id: string;
  name: string;
  description: string;
  is_archived: boolean;
  created_by: string;
  created_at: string;
  updated_at: string;
};

export type CommunicationConversation = {
  id: string;
  project_id: string | null;
  kind: "channel" | "dm" | "group_dm";
  name: string | null;
  description: string;
  primary_lead: CommunicationLeadReference | null;
  is_archived: boolean;
  member_ids: string[];
  updated_at: string;
};

export type CommunicationMessage = {
  id: string;
  conversation_id: string;
  parent_message_id: string | null;
  author: CommunicationMember;
  body: Record<string, unknown>;
  plaintext: string;
  mentioned_user_ids: string[];
  leads: CommunicationLeadReference[];
  attachments: CommunicationAttachment[];
  is_pinned: boolean;
  edited_at: string | null;
  deleted_at: string | null;
  created_at: string;
};

export type CommunicationAttachment = {
  id: string;
  message_id: string;
  kind: "file" | "voice";
  filename: string;
  content_type: string;
  byte_size: number;
  duration_ms: number | null;
  created_at: string;
  download_url: string;
};

export type CommunicationBootstrap = {
  active_user_id: string;
  projects: CommunicationProject[];
  conversations: CommunicationConversation[];
  members: CommunicationMember[];
};

export type CommunicationNotification = {
  id: string;
  kind: "mention" | "dm" | "thread_reply" | string;
  message_id: string;
  read_at: string | null;
  created_at: string;
};

export type CommunicationLinkPreview = {
  url: string;
  domain: string;
  title: string | null;
  description: string | null;
  image_url: string | null;
};

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
  source: "system_default" | "workspace" | "not_configured";
  version_number: number | null;
  status: "draft" | "published" | "archived" | "not_configured";
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
  follow_up_strategy: string[];
  conversion_goal: string;
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
  requested_by: string;
  request_origin: string;
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
  /** Internal compatibility field; do not display sequential lead numbers in the UI. */
  lead_number: number;
  workspace_id: string;
  owner_id?: string | null;
  owner_name?: string | null;
  source: string;
  source_detail: string | null;
  external_id: string | null;
  is_generated: boolean;
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
  updated_at: string;
  has_unread_update?: boolean;
  qualification: Qualification | null;
  analysis: LeadAnalysis | null;
  routing: RoutingDecision | null;
  crm_sync: CrmSync | null;
  confirmation_email?: ConfirmationEmail | null;
  outcome?: LeadOutcome | null;
  review_reasons?: string[];
};

export type LeadAssignmentOption = {
  user_id: string;
  display_name: string;
  role: string;
};

export type LeadOutcome = {
  pipeline_stage: string;
  opportunity_value?: number | null;
  conversion_type: "project" | "sale" | null;
  send_direct_message: boolean;
  credited_user_id: string | null;
  credited_name: string | null;
  closed_revenue: number | null;
  currency: string;
  closed_at: string | null;
  notifications: Array<{
    destination: "channel" | "direct_message";
    status: string;
    error_message: string | null;
    attempts: number;
  }>;
};

export type ConfirmationEmail = {
  status: string;
  recipient: string;
  attempts: number;
  external_message_id: string | null;
  error_message: string | null;
  sent_at: string | null;
};

export type FollowUpEmail = {
  id: string;
  lead_id: string;
  kind: string;
  recipient: string;
  subject: string;
  body: string | null;
  cc: string;
  bcc: string;
  html_body: string | null;
  attachments: GmailAttachmentRequest[];
  thread_id: string | null;
  in_reply_to: string | null;
  connection_id: string | null;
  rationale: string | null;
  generation_method: "openai" | "template" | "meeting_request" | null;
  generation_note: string | null;
  status: string;
  attempts: number;
  error_message: string | null;
  external_message_id: string | null;
  approved_by: string | null;
  approved_at: string | null;
  sent_at: string | null;
  created_at: string;
  updated_at: string;
};

export type WorkspaceEmailTemplate = {
  kind: "confirmation" | "follow_up";
  subject: string;
  body: string;
  generation_method: "manual" | "ai" | "ai_full";
};

export type LeadPipelineEvent = {
  id: string;
  stage: string;
  status: string;
  details: Record<string, unknown>;
  created_at: string;
};

export type MeetingProvider = "google_calendar" | "calendly";

export type WorkspaceCalendarEvent = {
  id: string;
  kind: "meeting" | "call" | "event";
  source: "manual" | MeetingProvider;
  title: string;
  start_at: string;
  end_at: string;
  location: string | null;
  notes: string | null;
  lead_id: string | null;
  lead_name: string | null;
  company_name: string | null;
  event_url: string | null;
  editable: boolean;
};

export type MeetingSuggestion = {
  provider: MeetingProvider;
  connection_id: string;
  account_name: string;
  event_type_uri: string | null;
  event_type_name: string | null;
  start_at: string;
  end_at: string;
  time_zone: string;
  source_message_id: string;
  source_gmail_connection_id: string | null;
};

export type MeetingIntegrationState = {
  provider: MeetingProvider;
  connected: boolean;
  account_name: string | null;
  message: string | null;
};

export type MeetingCalendlyEventType = {
  event_type_uri: string;
  event_type_name: string;
  status: "available" | "no_times" | "calendar_unavailable";
};

export type MeetingProviderError = {
  provider: MeetingProvider;
  message: string;
  retryable: boolean;
  code: string;
};

export type MeetingRecommendations = {
  lead_id: string;
  detected: boolean;
  message: string;
  source_message_id: string | null;
  offer_source_message_id: string | null;
  source_subject: string | null;
  source_excerpt: string | null;
  integrations: MeetingIntegrationState[];
  suggestions: MeetingSuggestion[];
  calendly_event_types: MeetingCalendlyEventType[];
  provider_errors: MeetingProviderError[];
  offer_status: "awaiting_send" | "sending" | "awaiting_confirmation" | "booking" | "booked" | "needs_attention" | "superseded" | null;
  offer_booking_error: string | null;
  offer_calendar_provider: MeetingProvider | null;
  offer_booking_link_mode: boolean;
  offer_has_confirmed_selection: boolean;
  offer_can_be_replaced: boolean;
  booked_meeting_at: string | null;
  cancelled_meeting_at?: string | null;
  cancelled_at?: string | null;
  cancellation_reason?: string | null;
};

export type ManagedMeeting = {
  id: string;
  provider: MeetingProvider;
  title: string;
  start_at: string;
  end_at: string;
  time_zone: string;
  event_url: string | null;
  reschedule_url: string | null;
  cancel_url: string | null;
  can_cancel_here: boolean;
};

export type SavedMeetingStatus = {
  status: "scheduled" | "cancelled";
  start_at: string | null;
  title: string;
  provider: MeetingProvider | null;
  cancellation_reason: string | null;
};

export type MeetingOfferDraftResult = {
  draft_id: string;
  status: "draft";
};

export type RetryConfirmedMeetingBookingResult = {
  status: string;
  message: string;
  booking_error: string | null;
};

export type ConfirmMeetingResult = {
  lead_id: string;
  provider: MeetingProvider;
  provider_event_id: string;
  provider_event_uri: string | null;
  event_url: string | null;
  title: string;
  start_at: string;
  end_at: string;
  time_zone: string;
  status: "scheduled";
};

export type LeadMomentumEvidence = {
  source: string;
  excerpt: string;
  observed_at: string | null;
};

export type LeadDeliveryCheckpoint = {
  destination: string;
  status: string;
  detail: string;
  attempted_at: string | null;
  needs_attention: boolean;
};

export type LeadMomentum = {
  score: number;
  classification: string;
  state: "new" | "warming" | "cooling" | "steady" | "awaiting_review";
  score_delta: number | null;
  previous_score: number | null;
  confidence: number | null;
  freshness_hours: number;
  observed_signals: number;
  last_signal_at: string | null;
  next_human_action: string;
  evidence: LeadMomentumEvidence[];
  delivery: LeadDeliveryCheckpoint[];
};

export type LeadProgressResult = {
  stage: string;
  note: string;
  classification: string;
  score: number;
  rationale: string[];
  updated_at: string;
};

export type GmailConversationSyncResult = {
  messages_added: number;
  profile_updated_fields: string[];
  threads_checked: number;
  threads_unreadable: number;
  truncated: boolean;
  classification: string;
  score: number;
};

export type LeadPayload = Omit<
  Lead,
  "id" | "lead_number" | "workspace_id" | "normalization_version" | "created_at" | "updated_at" | "has_unread_update" | "received_at" | "qualification" | "analysis" | "routing" | "crm_sync" | "confirmation_email" | "outcome" | "external_id" | "source_detail" | "is_generated"
> & {
  source: string;
  source_detail: string;
  external_id?: string;
};

export type LeadListResponse = {
  items: Lead[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
};

export type ProviderConnection = {
  id: string;
  workspace_id: string;
  provider_type: string;
  external_account_id: string | null;
  account_name: string;
  auth_type: string;
  status: "configured" | "connecting" | "awaiting_authorization" | "validating" | "connected" | "verified" | "expired" | "needs_reauthorization" | "error" | "needs_attention" | "authorization_expired" | "connection_failed" | "reconnect_required" | "disconnected";
  verification_status: "not_verified" | "verified" | "failed" | "reauthorization_required";
  scopes: string[];
  credential_state: "configured" | "missing" | "expired" | "invalid";
  configured: boolean;
  selected_model: string | null;
  last_validation_at: string | null;
  last_validation_result: Record<string, unknown>;
  metadata: Record<string, unknown>;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  owner_user_id?: string | null;
  connection_type?: "personal" | "team_shared" | "workspace_managed";
  allowed_actions?: string[];
};

export type ConnectionAccessGrant = {
  connection_id: string;
  membership_id: string;
  actions: string[];
  expires_at: string | null;
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

export type PermissionRegistryItem = {
  id: string;
  label: string;
};

export type PermissionRegistryCategory = {
  id: string;
  label: string;
  description: string;
  sensitive: boolean;
  feature_permissions: PermissionRegistryItem[];
  providers: PermissionRegistryItem[];
  provider_actions: PermissionRegistryItem[];
};

export type PermissionRegistry = {
  categories: PermissionRegistryCategory[];
  roles: Array<{
    id: string;
    label: string;
    default_permissions: string[];
  }>;
};

export type AccessPolicy = {
  scopes: Record<string, "none" | "own" | "team" | "all" | "custom">;
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
  access_policy: AccessPolicy;
  invited_by: string | null;
  invited_at: string | null;
  accepted_at: string | null;
  updated_at: string;
  slack_member_id?: string | null;
  slack_team_id?: string | null;
};

export type RoutingConfig = {
  workspace_id: string;
  default_strategy: "equal" | string;
  updated_at: string | null;
  manual_review: {
    personal_email: boolean;
    missing_person_name: boolean;
    missing_company_name: boolean;
    low_confidence: boolean;
    contradictory_evidence: boolean;
    missing_qualification_information: boolean;
    provider_failure: boolean;
  };
  members: Array<{
    user_id: string;
    display_name: string;
    email: string;
    role: string;
    weight: number;
    open_leads: number;
  }>;
};

export type PublicIntakeConfig = {
  workspace_id: string;
  url: string;
  created_at: string;
  rotated_at: string | null;
};

export type WorkspaceInvitation = {
  id: string;
  workspace_id: string;
  email: string;
  role: string;
  role_label: string;
  permission_overrides: PermissionOverrides;
  access_policy: AccessPolicy;
  status: "pending" | "accepted" | "expired" | "revoked" | string;
  invited_by: string;
  accepted_user_id: string | null;
  expires_at: string;
  accepted_at: string | null;
  revoked_at: string | null;
  created_at: string;
  updated_at: string;
  access_code?: string | null;
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
  connection_mode: "workspace" | "operator";
  supported_models: string[];
  default_model: string | null;
  directionality: {
    automatic_inbound: boolean;
    manual_outbound: boolean;
    automatic_outbound: boolean;
    outbound_action: boolean;
    request_response: boolean;
    description: string;
  };
  connection: ProviderConnection | null;
  connections: ProviderConnection[];
  /** Actions the current member may perform for this provider. */
  allowed_actions: string[];
};

export type LeadApprovalPolicy = {
  provider: string;
  display_name: string;
  auto_approve_leads: boolean;
  policy_source: "default" | "legacy_migration" | "user";
  verified_connection: boolean;
  updated_at: string | null;
};

export type SlackNotificationRule = {
  id: string;
  name: string;
  connection_id: string;
  destination_type: "channel" | "direct_message";
  channel_id: string | null;
  fallback_to_channel: boolean;
  queues: string[];
  qualification_bands: string[];
  sources: string[];
  weight: number;
};

export type SlackNotificationConfig = {
  workspace_id: string;
  default_connection_id: string | null;
  rules: SlackNotificationRule[];
  updated_at: string | null;
};

export type CrmInboundSyncState = {
  id: string;
  workspace_id: string;
  connection_id: string;
  provider: string;
  object_type: string;
  enabled: boolean;
  initial_import_completed: boolean;
  sync_interval_seconds: number;
  next_sync_at: string | null;
  status: string;
  last_successful_sync_at: string | null;
  records_processed: number;
  error_category: string | null;
  error_code: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
};

export type CrmInboundSyncRun = {
  id: string;
  status: string;
  mode: "initial" | "incremental" | "manual" | "review_recent" | "review_previous";
  created_at: string;
  error_category: string | null;
  error_code: string | null;
  last_error: string | null;
  records_fetched: number;
  records_created: number;
  records_updated: number;
  records_unchanged: number;
  records_skipped: number;
  records_failed: number;
  records_processed: number;
  records_succeeded: number;
};

export type CrmInboundSyncAccepted = {
  status: "queued";
  queued_at: string;
  message: string;
};

export type TimeRange = "weekly" | "monthly" | "annual";

export type ReasoningUsagePeriod = "weekly" | "monthly" | "quarterly" | "annual";
export type ReasoningUsageModel = {
  model: string;
  requests: number;
  successful_requests: number;
  failed_requests: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
};
export type ReasoningUsageProvider = {
  provider: "openai" | "gemini" | "claude" | "deepseek" | "xai";
  requests: number;
  successful_requests: number;
  failed_requests: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  models: ReasoningUsageModel[];
};
export type ReasoningUsage = {
  period: ReasoningUsagePeriod;
  starts_at: string;
  ends_at: string;
  timezone: string;
  providers: ReasoningUsageProvider[];
};

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
  active_pipeline_by_origin?: Array<{ origin: string; value: number }>;
  attention_leads: LeadCard[];
  recent_leads: LeadCard[];
};

export type ChannelAnalytics = {
  metadata: AnalyticsMetadata;
  channels: Array<{
    channel: string;
    lead_volume: number;
    share_percent: number;
    qualification_rate: number | null;
    qualified_leads?: number;
    booked_leads?: number;
    booked_rate?: number | null;
    qualified_to_booked_rate?: number | null;
    rate_sample_minimum?: number;
    closed_won_count?: number;
    scored_leads?: number;
    sql_conversion_rate: number | null;
    closed_won_conversion_rate: number | null;
    revenue: number;
    average_score: number;
    importance_index: number;
  }>;
};

export type PipelineStageAnalytics = {
  stage: string;
  label: string;
  count: number;
  value: number;
  leads: LeadCard[];
};

export type SalesPipelineStageAnalytics = Omit<PipelineStageAnalytics, "value"> & {
  value: null;
};

export type PipelineAnalytics = {
  metadata: AnalyticsMetadata;
  stages: PipelineStageAnalytics[];
  sales_stages: SalesPipelineStageAnalytics[];
  bands: Array<{ band: string; count: number }>;
  manual_review: number;
};

export type RevenueAnalytics = {
  metadata: AnalyticsMetadata;
  data_status: "unavailable" | "demo" | "crm" | "manual" | "mixed";
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
  members?: TeamMemberPerformance[];
  unattributed_leads?: number;
  cohort_description?: string;
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

export type TeamMemberPerformance = {
  user_id: string;
  display_name: string;
  role: string;
  status: string;
  assigned_leads: number;
  qualified_leads: number;
  closed_leads: number;
  project_wins: number;
  sale_wins: number;
  untyped_wins: number;
  lost_leads: number;
  active_leads: number;
  conversion_rate: number;
  revenue: number;
  wins_without_value: number;
  average_response_minutes: number | null;
  summary: string;
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
  insights: Array<{
    agent: string;
    summary: string;
    metrics: Array<{ key: string; label: string; value: number | string }>;
    details: string[];
  }>;
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
  items: Array<LeadCard & {
    review_reasons: string[];
    priority?: string;
    sla_overdue?: boolean;
    attention_score?: number;
  }>;
  total: number;
};

export type ExtendedAnalytics = {
  metadata: AnalyticsMetadata;
  speed_to_qualify: {
    average_minutes: number | null;
    median_minutes: number | null;
    sample_size: number;
  };
  qualified_to_booked: {
    qualified: number;
    booked: number;
    rate: number | null;
  };
  sla: {
    configured: number;
    compliant: number;
    breached: number;
    pending: number;
    unverified: number;
    rate: number | null;
    definition?: string;
  };
  escalations: Array<LeadCard & {
    sla_target_at: string;
    hours_overdue: number;
    priority: string;
    late_response: boolean;
  }>;
  stale_leads: Array<LeadCard & {
    last_activity_at: string;
    inactive_days: number;
  }>;
  calibration: Array<{
    score_band: string;
    closed: number;
    won: number;
    win_rate: number | null;
  }>;
  sales_cycle_by_segment: Array<{
    segment: string;
    sample_size: number;
    average_days: number;
    median_days: number;
  }>;
  icp_fit_distribution: Array<{
    criterion: string;
    statuses: Record<string, number>;
    evaluated: number;
  }>;
  response_heatmap: Array<{
    weekday: number;
    hour: number;
    inbound: number;
    outbound: number;
  }>;
  stage_velocity: Array<{
    transition: string;
    sample_size: number;
    median_hours: number;
  }>;
  channel_mix: Array<{ source: string; month: string; leads: number }>;
  team_utilization_trend: Array<{ month: string; rep: string; assignments: number }>;
  quarterly_scorecard: Array<{
    metric: string;
    current: number | null;
    previous: number | null;
  }>;
  revenue_by_conversion_type: Array<{ type: string; revenue: number; won: number }>;
  competitive_mentions: Array<{ mention: string; count: number }>;
  data_gaps: Array<{ key: string; label: string; reason: string }>;
};

export type AdvancedAnalytics = {
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
  channels: Array<{
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
  }>;
  cohorts: Array<{
    week: string;
    campaign: string;
    leads: number;
    qualified: number;
    won: number;
    lost: number;
  }>;
  members: Array<{
    user_id: string;
    name: string;
    closed: number;
    wins: number;
    comparable_leads: number;
    expected_wins: number;
    adjusted_win_index: number | null;
  }>;
  summary: string;
  summary_method: string;
  fairness_method: string;
  sample_size: number;
  truncated: boolean;
};

export type GmailMailboxMessage = {
  message_id: string;
  thread_id: string;
  sender: string;
  sender_name: string;
  reply_to?: string;
  recipients: string;
  subject: string;
  sent_at: string;
  snippet: string;
  direction: "inbound" | "outbound" | string;
  unread: boolean;
};

export type GmailThreadMessage = {
  message_id: string;
  thread_id: string;
  sender: string;
  sender_name: string;
  reply_to?: string;
  recipients: string;
  subject: string;
  sent_at: string;
  rfc_message_id: string;
  direction: "inbound" | "outbound" | string;
  body: string;
  attachments?: GmailMailboxAttachment[];
};

export type GmailMailboxAttachment = {
  attachment_id: string;
  filename: string;
  mime_type: string;
  size: number;
};

export type GmailMailboxMessagePage = {
  messages: GmailMailboxMessage[];
  next_page_token: string | null;
};

export type GmailSendRequest = {
  connection_id: string;
  recipient: string;
  cc?: string;
  bcc?: string;
  subject: string;
  body: string;
  html_body?: string;
  attachments?: GmailAttachmentRequest[];
  lead_id?: string;
  thread_id?: string;
  in_reply_to?: string;
  draft_id?: string;
};

export type WorkspaceEmailDraftInput = {
  connection_id?: string | null;
  recipient: string;
  cc: string;
  bcc: string;
  subject: string;
  body: string;
  html_body?: string | null;
  attachments: GmailAttachmentRequest[];
  thread_id?: string | null;
  in_reply_to?: string | null;
  lead_id?: string | null;
  expected_version?: number;
};

export type WorkspaceEmailDraft = {
  id: string;
  version: number;
  data: WorkspaceEmailDraftInput;
  created_at: string;
  updated_at: string;
};

export type WorkspaceEmailDraftSummary = {
  id: string;
  version: number;
  subject: string;
  recipient: string;
  updated_at: string;
};

export type GmailSendAccount = {
  id: string;
  account_name: string;
  sender_email: string;
};

export type GmailAttachmentRequest = {
  filename: string;
  content_type: string;
  content_base64: string;
};

export type GmailSendResult = {
  message_id: string;
  thread_id: string | null;
  sender: string;
  recipient: string;
  sent_at: string;
};

