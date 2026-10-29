import { useEffect, useState } from "react";
import { listLeads } from "../api";
import {
  opsGet,
  opsSave,
  type Consent,
  type Policy,
  type PolicyResponse,
} from "../operationsApi";
import type { Lead } from "../types";
import { confirmAction } from "../v3/ui/confirm";
type Retention = {
  lead_ids: string[];
  count: number;
  proof: string;
  scope: string;
  more: boolean;
};
export function ComplianceView() {
  const [policy, setPolicy] = useState<PolicyResponse | null>(null);
  const [events, setEvents] = useState<Consent[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [consent, setConsent] = useState({
    email: "",
    channel: "all",
    status: "denied",
    basis: "objection",
    purpose: "",
    evidence: "",
  });
  const [plan, setPlan] = useState<Retention | null>(null);
  const [leadSearch, setLeadSearch] = useState("");
  const [leads, setLeads] = useState<Lead[]>([]);
  const [leadId, setLeadId] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    opsGet<PolicyResponse>("/v1/ops/policy", controller.signal)
      .then(setPolicy)
      .catch((reason) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    return () => controller.abort();
  }, [revision]);
  useEffect(() => {
    const controller = new AbortController();
    opsGet<{ items: Consent[]; total: number }>(
      `/v1/ops/consent?search=${encodeURIComponent(search)}&offset=${offset}`,
      controller.signal,
    )
      .then((data) => {
        setEvents(data.items);
        setTotal(data.total);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    return () => controller.abort();
  }, [search, offset, revision]);
  useEffect(() => {
    const controller = new AbortController();
    listLeads({ search: leadSearch, limit: 20 }, controller.signal)
      .then((data) => setLeads(data.items))
      .catch(() => {});
    return () => controller.abort();
  }, [leadSearch]);
  async function action(task: () => Promise<unknown>, message: string) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await task();
      setNotice(message);
      setRevision((value) => value + 1);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Unable to complete action",
      );
    } finally {
      setBusy(false);
    }
  }
  function settings(patch: Partial<Policy>) {
    if (policy)
      setPolicy({
        ...policy,
        configuration: { ...policy.configuration, ...patch },
      });
    setPlan(null);
  }
  async function exportAudit() {
    const data = await opsGet<Record<string, unknown>>(
      `/v1/ops/leads/${leadId}/audit-export`,
    );
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = `corral-audit-${leadId}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return (
    <div className="page-view ops-page">
      <header className="ops-page-head">
        <div>
          <span className="eyebrow">Consent and evidence</span>
          <h1>Compliance controls</h1>
          <p>
            Record contact permissions, enforce outreach suppression, export
            decision history and review retention.
          </p>
        </div>
      </header>
      {error && (
        <p role="alert" className="team-alert">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="ops-success">
          {notice}
        </p>
      )}
      <section className="ops-panel">
        <h2>Record permission or do-not-contact</h2>
        <p>
          A denial or withdrawal for all channels blocks drafting and sending
          even if one channel has a separate grant.
        </p>
        <form
          className="ops-form ops-consent-form"
          onSubmit={(event) => {
            event.preventDefault();
            void action(
              () => opsSave("/v1/ops/consent", consent),
              "Permission recorded.",
            );
          }}
        >
          <div className="ops-form-grid">
            <label>
              Contact email
              <input
                type="email"
                required
                value={consent.email}
                onChange={(event) =>
                  setConsent({ ...consent, email: event.target.value })
                }
              />
            </label>
            <label>
              Channel
              <select
                value={consent.channel}
                onChange={(event) =>
                  setConsent({ ...consent, channel: event.target.value })
                }
              >
                {["all", "email", "sms", "whatsapp", "call", "research"].map(
                  (value) => (
                    <option key={value}>{value}</option>
                  ),
                )}
              </select>
            </label>
            <label>
              Status
              <select
                value={consent.status}
                onChange={(event) =>
                  setConsent({ ...consent, status: event.target.value })
                }
              >
                {["denied", "withdrawn", "granted"].map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </label>
            <label>
              Authorization basis
              <select
                value={consent.basis}
                onChange={(event) =>
                  setConsent({ ...consent, basis: event.target.value })
                }
              >
                {[
                  "objection",
                  "consent",
                  "legitimate_interest",
                  "contract",
                ].map((value) => (
                  <option value={value} key={value}>
                    {value.replaceAll("_", " ")}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Purpose
              <input
                required
                value={consent.purpose}
                maxLength={255}
                onChange={(event) =>
                  setConsent({ ...consent, purpose: event.target.value })
                }
              />
            </label>
            <label>
              Evidence
              <textarea
                required
                maxLength={4000}
                value={consent.evidence}
                onChange={(event) =>
                  setConsent({ ...consent, evidence: event.target.value })
                }
                placeholder="What the contact authorized or requested, and where it was recorded"
              />
            </label>
          </div>
          <button className="primary-button" disabled={busy}>
            Record permission
          </button>
        </form>
      </section>
      {policy && (
        <section className="ops-panel">
          <h2>Workspace safeguards</h2>
          <form
            className="ops-form"
            onSubmit={(event) => {
              event.preventDefault();
              void action(
                () =>
                  opsSave(
                    "/v1/ops/policy",
                    {
                      configuration: policy.configuration,
                      version: policy.version,
                    },
                    "PUT",
                  ),
                "Safeguards saved.",
              );
            }}
          >
            <label className="ops-check">
              <input
                type="checkbox"
                checked={policy.configuration.require_recorded_consent}
                onChange={(event) =>
                  settings({ require_recorded_consent: event.target.checked })
                }
              />
              Require recorded permission before drafting or sending outreach
            </label>
            <label>
              Required hosting region
              <select
                value={policy.configuration.required_region}
                onChange={(event) =>
                  settings({ required_region: event.target.value })
                }
              >
                {["any", "local", "eu", "uk", "gcc", "pk", "us"].map(
                  (value) => (
                    <option key={value}>{value}</option>
                  ),
                )}
              </select>
            </label>
            <p>
              Current hosting region: {policy.hosting_region}. A region
              requirement is enforced against hosting configuration; it does not
              relocate existing data.
            </p>
            <label>
              Inactive lead retention, days
              <input
                type="number"
                min={30}
                max={3650}
                value={policy.configuration.retention_days ?? ""}
                onChange={(event) =>
                  settings({
                    retention_days: event.target.value
                      ? Number(event.target.value)
                      : null,
                  })
                }
                placeholder="No automatic expiry"
              />
            </label>
            <p className="ops-muted">
              Retention requires an explicit reviewed deletion plan. Active
              automations, independent contacts and consent ledgers are
              retained.
            </p>
            <button className="primary-button" disabled={busy}>
              Save safeguards
            </button>
          </form>
        </section>
      )}
      <section className="ops-panel">
        <h2>Permission history</h2>
        <label>
          Search email
          <input
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setOffset(0);
            }}
          />
        </label>
        <div className="ops-table-wrap" role="region" aria-label="Table; scroll horizontally to view all columns" tabIndex={0}>
          <table className="ops-table">
            <thead>
              <tr>
                <th>Contact</th>
                <th>Channel</th>
                <th>Status</th>
                <th>Basis / purpose</th>
                <th>Recorded</th>
              </tr>
            </thead>
            <tbody>
              {!events.length && <tr><td colSpan={5}><div className="ops-empty-inline"><strong>No permissions recorded</strong><span>Recorded permissions and objections will appear here.</span></div></td></tr>}
              {events.map((event) => (
                <tr key={event.id}>
                  <td>{event.email}</td>
                  <td>{event.channel}</td>
                  <td>{event.status}</td>
                  <td>
                    {event.basis.replaceAll("_", " ")} · {event.purpose}
                    <details>
                      <summary>Evidence</summary>
                      {event.evidence}
                    </details>
                  </td>
                  <td>{new Date(event.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="ops-toolbar">
          <span>{total} records</span>
          <button
            className="button-quiet"
            disabled={!offset}
            onClick={() => setOffset(Math.max(0, offset - 50))}
          >
            Previous
          </button>
          <button
            className="button-quiet"
            disabled={offset + 50 >= total}
            onClick={() => setOffset(offset + 50)}
          >
            Next
          </button>
        </div>
      </section>
      <section className="ops-panel">
        <h2>Complete lead audit export</h2>
        <div className="ops-form-grid">
          <label>
            Find a lead
            <input
              value={leadSearch}
              onChange={(event) => setLeadSearch(event.target.value)}
              placeholder="Name, company or email"
            />
          </label>
          <label>
            Lead
            <select
              value={leadId}
              onChange={(event) => setLeadId(event.target.value)}
            >
              <option value="">Select a lead</option>
              {leads.map((lead) => (
                <option value={lead.id} key={lead.id}>
                  {lead.full_name} · {lead.company_name}
                </option>
              ))}
            </select>
          </label>
        </div>
        <p>
          Includes recorded qualification, routes, notifications, CRM handoffs,
          emails, permissions, notes and automation approvals.
        </p>
        <button
          className="secondary-button"
          disabled={!leadId || busy}
          onClick={() => void action(exportAudit, "Audit export downloaded.")}
        >
          Download audit JSON
        </button>
      </section>
      <section className="ops-panel">
        <h2>Review retention plan</h2>
        <button
          className="secondary-button"
          disabled={busy}
          onClick={() =>
            void action(
              async () => setPlan(await opsGet<Retention>("/v1/ops/retention")),
              "Retention plan loaded.",
            )
          }
        >
          Preview eligible records
        </button>
        {plan && (
          <div>
            <p>
              {plan.count} inactive leads eligible. {plan.scope}
            </p>
            {plan.more && (
              <p>
                The plan is capped at 500 leads; review another batch after this
                one.
              </p>
            )}
            <details>
              <summary>Review lead identifiers</summary>
              <ul>
                {plan.lead_ids.map((id) => (
                  <li key={id}>{id}</li>
                ))}
              </ul>
            </details>
            <button
              className="secondary-button"
              disabled={!plan.count || busy}
              onClick={() => {
                void confirmAction({
                  title: `Permanently delete ${plan.count} inactive lead${plan.count === 1 ? "" : "s"}?`,
                  message: "Their dependent history is deleted too. Export anything you need first. This can't be undone from Corral.",
                  confirmLabel: "Delete permanently",
                  tone: "danger",
                }).then((confirmed) => {
                  if (!confirmed) return;
                  void action(async () => {
                    await opsSave("/v1/ops/retention/apply", {
                      proof: plan.proof,
                      confirm_permanent_deletion: true,
                    });
                    setPlan(null);
                  }, "Retention plan applied.");
                });
              }}
            >
              Apply reviewed deletion plan
            </button>
          </div>
        )}
      </section>
      <p className="ops-muted">
        These controls record and enforce your configured policy. They do not
        certify legal compliance.
      </p>
    </div>
  );
}
