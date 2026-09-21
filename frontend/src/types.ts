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
  rationale: string[];
  score_breakdown: ScoreLine[];
  component_scores: Record<string, number>;
  policy_version: string;
  confidence: number;
  created_at: string | null;
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

export type Lead = {
  id: string;
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
};

export type LeadPayload = Omit<
  Lead,
  "id" | "normalization_version" | "created_at" | "received_at" | "qualification" | "analysis" | "routing" | "external_id" | "source_detail"
> & {
  source: string;
  source_detail: string;
  external_id?: string;
};
