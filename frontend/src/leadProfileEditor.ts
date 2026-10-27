import type { Lead } from "./types";

export type LeadProfileDraft = {
  full_name: string;
  email: string;
  phone: string;
  job_title: string;
  company_name: string;
  industry: string;
  employee_count: string;
  annual_revenue: string;
  geography: string;
  budget: string;
  product_or_service: string;
};

export type LeadProfileUpdates = Partial<Pick<
  Lead,
  | "full_name"
  | "email"
  | "phone"
  | "job_title"
  | "company_name"
  | "industry"
  | "employee_count"
  | "annual_revenue"
  | "geography"
  | "budget"
  | "product_or_service"
>>;

export function createLeadProfileDraft(lead: Lead): LeadProfileDraft {
  return {
    full_name: lead.full_name || "",
    email: lead.email || "",
    phone: lead.phone || "",
    job_title: lead.job_title || "",
    company_name: lead.company_name || "",
    industry: lead.industry || "",
    employee_count: lead.employee_count == null ? "" : String(lead.employee_count),
    annual_revenue: lead.annual_revenue == null ? "" : String(lead.annual_revenue),
    geography: lead.geography || "",
    budget: lead.budget == null ? "" : String(lead.budget),
    product_or_service: lead.product_or_service || "",
  };
}

export function buildLeadProfileUpdates(
  lead: Lead,
  draft: LeadProfileDraft,
): { updates: LeadProfileUpdates; error?: string } {
  const updates: Record<string, unknown> = {};
  const requiredTextFields = new Set(["full_name", "email", "company_name", "product_or_service"]);
  const labels: Record<string, string> = {
    full_name: "Contact name",
    email: "Email",
    phone: "Phone",
    job_title: "Job title",
    company_name: "Company name",
    industry: "Industry",
    geography: "Geography",
    product_or_service: "Product or service",
  };

  for (const field of [
    "company_name",
    "full_name",
    "job_title",
    "email",
    "phone",
    "industry",
    "geography",
    "product_or_service",
  ] as const) {
    const value = draft[field].trim();
    const normalizedValue = field === "email" ? value.toLowerCase() : value;
    const current = String(lead[field] || "").trim();
    const normalizedCurrent = field === "email" ? current.toLowerCase() : current;
    if (normalizedValue === normalizedCurrent) continue;
    if (requiredTextFields.has(field) && !normalizedValue) {
      return { updates: {}, error: `${labels[field]} cannot be blank.` };
    }
    updates[field] = normalizedValue || null;
  }

  const numericFields = [
    { key: "employee_count", label: "Employee count", raw: draft.employee_count, current: lead.employee_count, integer: true },
    { key: "annual_revenue", label: "Annual revenue", raw: draft.annual_revenue, current: lead.annual_revenue, integer: false },
    { key: "budget", label: "Budget", raw: draft.budget, current: lead.budget, integer: false },
  ] as const;
  for (const field of numericFields) {
    const raw = field.raw.trim();
    const value = raw ? Number(raw) : null;
    if (value !== null && (!Number.isFinite(value) || value < 0 || (field.integer && !Number.isInteger(value)))) {
      return { updates: {}, error: `${field.label} must be ${field.integer ? "a non-negative whole number" : "a non-negative number"}.` };
    }
    if (value !== field.current) updates[field.key] = value;
  }

  return { updates: updates as LeadProfileUpdates };
}
