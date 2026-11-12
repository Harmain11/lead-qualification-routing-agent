import assert from "node:assert/strict";
import test from "node:test";
import {
  applyProviderDecision,
  categoryAccessSummary,
  permissionDisplayState,
  providerActionSummary,
} from "../src/permissionEditorModel.ts";
import { canSubAssignLeads } from "../src/permissions.ts";
import type { PermissionRegistryCategory } from "../src/types.ts";

const category: PermissionRegistryCategory = {
  id: "crm",
  label: "CRM & records",
  description: "CRM access",
  sensitive: true,
  feature_permissions: [{ id: "crm.sync", label: "Sync CRM" }],
  providers: [
    { id: "salesforce", label: "Salesforce" },
    { id: "hubspot", label: "HubSpot" },
  ],
  provider_actions: [
    { id: "view", label: "View connection" },
    { id: "sync", label: "Sync records" },
  ],
};

test("provider bulk edits change selected providers only", () => {
  const original = {
    grants: ["integrations.hubspot.sync", "integrations.gmail.view"],
    denies: ["integrations.salesforce.view", "integrations.outlook.send"],
  };
  const next = applyProviderDecision(original, ["salesforce"], "sync", "grant");

  assert.deepEqual(next.grants, [
    "integrations.gmail.view",
    "integrations.hubspot.sync",
    "integrations.salesforce.sync",
  ]);
  assert.deepEqual(next.denies, [...original.denies].sort());
});

test("clearing a selected provider action preserves every unrelated exception", () => {
  const original = {
    grants: ["integrations.salesforce.sync", "integrations.hubspot.sync"],
    denies: ["integrations.salesforce.view", "integrations.gmail.send"],
  };
  const next = applyProviderDecision(original, ["salesforce"], "sync", "inherit");

  assert.deepEqual(next.grants, ["integrations.hubspot.sync"]);
  assert.deepEqual(next.denies, [...original.denies].sort());
});

test("shared provider controls report mixed inherited and exception states", () => {
  const inherited = new Set(["integrations.salesforce.view"]);
  const overrides = {
    grants: ["integrations.hubspot.view"],
    denies: [],
  };
  assert.equal(
    providerActionSummary(overrides, inherited, ["salesforce", "hubspot"], "view"),
    "mixed",
  );
  assert.equal(
    permissionDisplayState("integrations.hubspot.view", overrides, inherited),
    "exception-grant",
  );
});

test("category counts granted permissions and providers instead of raw toggles", () => {
  const inherited = new Set([
    "crm.sync",
    "integrations.salesforce.view",
    "integrations.salesforce.sync",
  ]);
  const summary = categoryAccessSummary(
    category,
    { grants: [], denies: ["integrations.salesforce.sync"] },
    inherited,
  );
  assert.deepEqual(summary, {
    activePermissions: 1,
    totalPermissions: 1,
    activeProviders: 1,
    totalProviders: 2,
  });
});

test("lead sub-assignment is limited to owners, admins, and managers", () => {
  const granted = new Set(["leads.reassign"]);
  assert.equal(canSubAssignLeads("owner", granted), true);
  assert.equal(canSubAssignLeads("admin", granted), true);
  assert.equal(canSubAssignLeads("sales_manager", granted), true);
  assert.equal(canSubAssignLeads("sales_rep", granted), false);
  assert.equal(canSubAssignLeads("sales_manager", new Set()), false);
});
