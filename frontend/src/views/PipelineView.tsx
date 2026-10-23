import { useState } from "react";
import { Bars } from "../components/Charts";
import type { PipelineAnalytics } from "../types";
import { Empty, LeadRow, money, Panel, Unavailable } from "./productShared";
import "./PipelineView.css";

export function PipelineView({
  data,
  onInspect,
}: {
  data: PipelineAnalytics | null;
  onInspect: (id: string) => void;
}) {
  const [mode, setMode] = useState<"qualification" | "sales">("qualification");
  const [active, setActive] = useState("received");
  if (!data) return <Unavailable />;
  const stages = mode === "sales" ? data.sales_stages : data.stages;
  const selected = stages.find((stage) => stage.stage === active) ?? stages[0];
  if (!selected) return <Unavailable />;
  const totalLeads = mode === "sales"
    ? data.sales_stages.reduce((total, stage) => total + stage.count, 0)
    : data.stages.find((stage) => stage.stage === "received")?.count ?? 0;
  const activeSalesCount = data.sales_stages
    .filter((stage) => !["closed_won", "closed_lost"].includes(stage.stage))
    .reduce((total, stage) => total + stage.count, 0);
  const opportunityValue =
    data.stages.find((stage) => stage.stage === "opportunity")?.value ?? null;
  return (
    <div className="page-view operational-page">
      <header className="page-hero compact-hero">
        <div>
          <span className="eyebrow">
            {mode === "sales" ? "Sales activity" : "Commercial motion map"}
          </span>
          <h1>Pipeline</h1>
          <p>
            {mode === "sales"
              ? "Track leads from first contact through follow-up, meetings, proposals, and recorded outcomes."
              : "Inspect every stage and the qualification state that shaped progression."}
          </p>
        </div>
        <div className="pipeline-total">
          <span>{mode === "sales" ? "Leads in progress" : "Active pipeline"}</span>
          <strong>
            {mode === "sales"
              ? activeSalesCount
              : opportunityValue === null
                ? "—"
                : money(opportunityValue)}
          </strong>
        </div>
      </header>
      <div className="pipeline-view-toggle" role="group" aria-label="Pipeline type">
        <button
          id="pipeline-qualification-tab"
          type="button"
          aria-pressed={mode === "qualification"}
          aria-controls="pipeline-view-panel"
          onClick={() => {
            setMode("qualification");
            setActive("received");
          }}
        >
          Lead qualification
        </button>
        <button
          id="pipeline-sales-tab"
          type="button"
          aria-pressed={mode === "sales"}
          aria-controls="pipeline-view-panel"
          onClick={() => {
            setMode("sales");
            setActive("new_lead");
          }}
        >
          Sales pipeline
        </button>
      </div>
      {totalLeads === 0 && (
        <section className="panel workspace-empty">
          <Empty
            title={mode === "sales" ? "Sales pipeline is ready" : "Pipeline is ready"}
            text={
              mode === "sales"
                ? "New leads will appear here as connected sources begin sending records."
                : "Qualified leads will appear here as connected sources begin sending records."
            }
          />
        </section>
      )}
      <div
        id="pipeline-view-panel"
        className="pipeline-view-panel"
      >
        <section
          className={`pipeline-track panel${mode === "sales" ? " pipeline-track-sales" : ""}`}
          data-tour="pipeline"
        >
          {stages.map((stage, index) => (
            <button
              key={stage.stage}
              type="button"
              className={active === stage.stage ? "active" : ""}
              aria-pressed={active === stage.stage}
              onClick={() => setActive(stage.stage)}
            >
              <span>{String(index + 1).padStart(2, "0")}</span>
              <b>{stage.label}</b>
              <strong>{stage.count}</strong>
              <small>
                {mode === "qualification"
                  ? stage.value !== null && stage.value > 0
                    ? money(stage.value)
                    : "—"
                  : ""}
              </small>
              <span className="pipeline-lead-dots" aria-hidden="true">
                {stage.leads.slice(0, 4).map((lead) => (
                  <i key={lead.id} />
                ))}
              </span>
              {mode === "qualification" && index < stages.length - 1 && (
                <i className="pipeline-link" />
              )}
            </button>
          ))}
        </section>
        <section
          className="pipeline-inspector pipeline-detail-enter"
          key={`${mode}-${selected.stage}`}
        >
          <Panel
            title={`${selected.label} leads`}
            eyebrow="Underlying workspace records"
            action={`${selected.count} total`}
          >
            {selected.leads.length ? (
              <div className="compact-leads">
                {selected.leads.map((lead) => (
                  <LeadRow
                    lead={lead}
                    key={lead.id}
                    onClick={() => onInspect(lead.id)}
                  />
                ))}
              </div>
            ) : (
              <Empty
                title="Stage is clear"
                text="No leads occupy this stage in the selected period."
              />
            )}
          </Panel>
          <Panel title="Qualification state" eyebrow="Decision distribution">
            <Bars
              rows={data.bands.map((row) => ({
                label: row.band,
                value: row.count,
              }))}
            />
          </Panel>
        </section>
      </div>
    </div>
  );
}
