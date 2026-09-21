import type { Lead, LeadPayload } from "./types";

export async function createLead(payload: LeadPayload): Promise<Lead> {
  const response = await fetch("/v1/leads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: "Unable to create lead" }));
    throw new Error(error.detail ?? "Unable to create lead");
  }
  return response.json() as Promise<Lead>;
}

export async function listLeads(): Promise<Lead[]> {
  const response = await fetch("/v1/leads");
  if (!response.ok) throw new Error("Unable to load leads");
  const body = (await response.json()) as { items: Lead[] };
  return body.items;
}
