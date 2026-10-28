import assert from "node:assert/strict";
import test from "node:test";
import type { Lead } from "../src/types.ts";
import { buildLeadProfileUpdates, createLeadProfileDraft } from "../src/leadProfileEditor.ts";

function makeLead(overrides: Partial<Lead> = {}): Lead {
  return {
    id: "lead-1",
    full_name: "Mina Chen",
    email: "mina@example.com",
    phone: "+1 555 0100",
    job_title: "Director",
    company_name: "Acme",
    industry: "Software",
    employee_count: 12,
    annual_revenue: 500000,
    geography: "North America",
    budget: 25000,
    product_or_service: "CRM",
    ...overrides,
  } as Lead;
}

test("profile editor sends only changed lead details", () => {
  const lead = makeLead();
  const draft = createLeadProfileDraft(lead);
  draft.company_name = "Acme Systems";
  draft.job_title = "VP";
  draft.email = "MINA@EXAMPLE.COM";

  assert.deepEqual(buildLeadProfileUpdates(lead, draft), {
    updates: { company_name: "Acme Systems", job_title: "VP" },
  });
});

test("profile editor clears optional fields without blanking required identity fields", () => {
  const lead = makeLead();
  const draft = createLeadProfileDraft(lead);
  draft.phone = "";
  draft.geography = "";
  draft.budget = "";

  assert.deepEqual(buildLeadProfileUpdates(lead, draft), {
    updates: { phone: null, geography: null, budget: null },
  });
});

test("missing existing required fields do not block edits to other details", () => {
  const lead = makeLead({ company_name: "" });
  const draft = createLeadProfileDraft(lead);
  draft.phone = "555-0101";

  assert.deepEqual(buildLeadProfileUpdates(lead, draft), { updates: { phone: "555-0101" } });
});

test("profile editor rejects clearing required fields and invalid numeric values", () => {
  const lead = makeLead();
  const blankCompany = createLeadProfileDraft(lead);
  blankCompany.company_name = "";
  assert.match(buildLeadProfileUpdates(lead, blankCompany).error || "", /Company name cannot be blank/);

  const invalidCount = createLeadProfileDraft(lead);
  invalidCount.employee_count = "1.5";
  assert.match(buildLeadProfileUpdates(lead, invalidCount).error || "", /non-negative whole number/);
});
