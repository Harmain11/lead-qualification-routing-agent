import { useEffect, useState } from "react";
import { listLeads } from "../api";
import { OperationsDialog } from "./OperationsDialog";
import {
  opsGet,
  opsSave,
  type Automation,
  type AutomationEnrollmentBatch,
} from "../operationsApi";

type LeadOption = { id: string; full_name: string; company_name: string };

export function AutomationEnrollmentDialog({
  kind,
  definition,
  fixedLeadIds,
  fixedLeadLabel,
  onClose,
  onOpenAutomations,
  onEnrolled,
}: {
  kind: "sequence" | "playbook";
  definition?: Automation;
  fixedLeadIds?: string[];
  fixedLeadLabel?: string;
  onClose: () => void;
  onOpenAutomations?: () => void;
  onEnrolled?: (result: AutomationEnrollmentBatch) => void;
}) {
  const [definitions, setDefinitions] = useState<Automation[]>([]);
  const [definitionId, setDefinitionId] = useState(definition?.id ?? "");
  const [definitionsLoading, setDefinitionsLoading] = useState(!definition);
  const [query, setQuery] = useState("");
  const [leads, setLeads] = useState<LeadOption[]>([]);
  const [selectedLeadIds, setSelectedLeadIds] = useState<string[]>(
    fixedLeadIds ?? [],
  );
  const [selectedLeadLabels, setSelectedLeadLabels] = useState<
    Record<string, string>
  >({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<AutomationEnrollmentBatch | null>(null);
  const chosenDefinition =
    definition ?? definitions.find((item) => item.id === definitionId);
  const isFixedLead = Boolean(fixedLeadIds?.length);

  useEffect(() => {
    if (definition) {
      setDefinitionId(definition.id);
      return;
    }
    const controller = new AbortController();
    setDefinitionsLoading(true);
    opsGet<{ items: Automation[] }>(
      `/v1/automations?kind=${kind}`,
      controller.signal,
    )
      .then(({ items }) => {
        const activeDefinitions = items.filter((item) => item.active);
        setDefinitions(activeDefinitions);
        setDefinitionId((current) =>
          activeDefinitions.some((item) => item.id === current)
            ? current
            : (activeDefinitions[0]?.id ?? ""),
        );
      })
      .catch((reason) => {
        if (!controller.signal.aborted) {
          setError(
            reason instanceof Error
              ? reason.message
              : "Unable to load active definitions",
          );
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setDefinitionsLoading(false);
      });
    return () => controller.abort();
  }, [definition, kind]);

  useEffect(() => {
    if (isFixedLead || result) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void listLeads({ search: query, limit: 20 }, controller.signal)
        .then((data) => setLeads(data.items))
        .catch((reason) => {
          if (!controller.signal.aborted) {
            setError(
              reason instanceof Error
                ? reason.message
                : "Unable to find leads",
            );
          }
        });
    }, 200);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [isFixedLead, query, result]);

  function toggleLead(lead: LeadOption) {
    if (selectedLeadIds.includes(lead.id)) {
      setSelectedLeadIds((current) =>
        current.filter((item) => item !== lead.id),
      );
      setSelectedLeadLabels((current) => {
        const next = { ...current };
        delete next[lead.id];
        return next;
      });
    } else if (selectedLeadIds.length < 100) {
      setSelectedLeadIds((current) => [...current, lead.id]);
      setSelectedLeadLabels((current) => ({
        ...current,
        [lead.id]: `${lead.full_name || "Unnamed lead"} · ${lead.company_name || "No company"}`,
      }));
    }
  }

  async function enroll() {
    if (!chosenDefinition || !selectedLeadIds.length) return;
    setBusy(true);
    setError("");
    try {
      const enrolled = await opsSave<AutomationEnrollmentBatch>(
        `/v1/automations/${chosenDefinition.id}/enroll-bulk`,
        { lead_ids: selectedLeadIds },
      );
      setResult(enrolled);
      onEnrolled?.(enrolled);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Unable to enroll leads",
      );
    } finally {
      setBusy(false);
    }
  }

  const title = definition
    ? `Enroll leads in ${definition.name}`
    : `Choose a ${kind} for this lead`;

  return (
    <OperationsDialog
      title={title}
      onClose={onClose}
      className="automation-enrollment-dialog"
    >
      <div className="automation-enrollment">
        {result ? (
          <>
            <p className="ops-enrollment-success" role="status">
              {result.enrolled} newly enrolled
              {result.enrolled === 1 ? " lead" : " leads"};{" "}
              {result.already_enrolled} already enrolled.
            </p>
            <p className="ops-muted">
              Scheduled follow-ups create drafts for review. They are not sent
              automatically.
            </p>
            <div className="ops-enrollment-actions">
              <button className="primary-button" onClick={onClose}>
                Done
              </button>
            </div>
          </>
        ) : (
          <>
            {!definition && (
              <section>
                <h3>Active {kind}s</h3>
                {definitionsLoading ? (
                  <p className="ops-muted">Loading active definitions…</p>
                ) : definitions.length ? (
                  <div className="ops-enrollment-options">
                    {definitions.map((item) => (
                      <label className="ops-enrollment-option" key={item.id}>
                        <input
                          type="radio"
                          name="automation-definition"
                          checked={definitionId === item.id}
                          onChange={() => setDefinitionId(item.id)}
                          disabled={busy}
                        />
                        <span>
                          <strong>{item.name}</strong>
                          <small>
                            {item.specification.nodes.length} steps ·{" "}
                            {item.specification.timezone}
                          </small>
                        </span>
                      </label>
                    ))}
                  </div>
                ) : !definitionsLoading ? (
                  <div className="ops-empty-inline">
                    <strong>No active {kind}s</strong>
                    <span>
                      Create and activate one in Sequences &amp; playbooks
                      before enrolling leads.
                    </span>
                    {onOpenAutomations && (
                      <button
                        className="secondary-button"
                        onClick={onOpenAutomations}
                      >
                        Open Sequences &amp; playbooks
                      </button>
                    )}
                  </div>
                ) : null}
              </section>
            )}

            {isFixedLead ? (
              <p className="ops-muted">
                Lead: {fixedLeadLabel || "Selected lead"}
              </p>
            ) : (
              <section>
                <h3>Select leads</h3>
                <label>
                  Find leads
                  <input
                    autoFocus
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="Search by name or company"
                    disabled={busy}
                  />
                </label>
                <p className="ops-muted">
                  Select up to 100 leads. Your selection stays checked as you
                  search.
                </p>
                <div className="ops-enrollment-leads">
                  {leads.map((lead) => (
                    <label className="ops-enrollment-lead" key={lead.id}>
                      <input
                        type="checkbox"
                        checked={selectedLeadIds.includes(lead.id)}
                        onChange={() => toggleLead(lead)}
                        disabled={
                          busy ||
                          (!selectedLeadIds.includes(lead.id) &&
                            selectedLeadIds.length >= 100)
                        }
                      />
                      <span>
                        <strong>{lead.full_name || "Unnamed lead"}</strong>
                        <small>{lead.company_name || "No company recorded"}</small>
                      </span>
                    </label>
                  ))}
                  {!leads.length && (
                    <p className="ops-muted">No matching leads found.</p>
                  )}
                </div>
                <p className="ops-muted">
                  {selectedLeadIds.length} of 100 leads selected
                </p>
                {!!selectedLeadIds.length && (
                  <div
                    className="ops-enrollment-selected"
                    aria-label="Selected leads"
                  >
                    {selectedLeadIds.map((id) => {
                      const label = selectedLeadLabels[id] || "Selected lead";
                      return (
                        <span className="ops-enrollment-chip" key={id}>
                          {label}
                          <button
                            type="button"
                            className="button-quiet"
                            aria-label={`Remove ${label}`}
                            disabled={busy}
                            onClick={() => {
                              setSelectedLeadIds((current) =>
                                current.filter((item) => item !== id),
                              );
                              setSelectedLeadLabels((current) => {
                                const next = { ...current };
                                delete next[id];
                                return next;
                              });
                            }}
                          >
                            ×
                          </button>
                        </span>
                      );
                    })}
                    <button
                      type="button"
                      className="button-quiet"
                      disabled={busy}
                      onClick={() => {
                        setSelectedLeadIds([]);
                        setSelectedLeadLabels({});
                      }}
                    >
                      Clear selection
                    </button>
                  </div>
                )}
              </section>
            )}

            {error && <p className="team-alert" role="alert">{error}</p>}
            <div className="ops-enrollment-actions">
              <button className="button-quiet" onClick={onClose} disabled={busy}>
                Cancel
              </button>
              <button
                className="primary-button"
                disabled={
                  busy ||
                  definitionsLoading ||
                  !chosenDefinition ||
                  !selectedLeadIds.length
                }
                onClick={() => void enroll()}
              >
                {busy
                  ? "Enrolling…"
                  : `Enroll ${selectedLeadIds.length || ""} ${
                      selectedLeadIds.length === 1 ? "lead" : "leads"
                    }`}
              </button>
            </div>
          </>
        )}
      </div>
    </OperationsDialog>
  );
}
