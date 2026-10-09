import { useState } from "react";
import { Bars } from "../components/Charts";
import type { PipelineAnalytics } from "../types";
import { Empty, LeadRow, money, Panel, Unavailable } from "./productShared";

export function PipelineView({
  data,
  onInspect,
}: {
  data: PipelineAnalytics | null;
  onInspect: (id: string) => void;
}) {
  const [active, setActive] = useState("received");
  if (!data) return <Unavailable />;
  const selected =
    data.stages.find((stage) => stage.stage === active) ?? data.stages[0];
  const totalLeads =
    data.stages.find((stage) => stage.stage === "received")?.count ?? 0;
  return (
    <div className="page-view operational-page">
      <header className="page-hero compact-hero">
        <div>
          <span className="eyebrow">Commercial motion map</span>
          <h1>
            Pipeline, without
            <br />
            <em>the blind spots.</em>
          </h1>
          <p>
            Inspect every stage and the qualification state that shaped
            progression.
          </p>
        </div>
        <div className="pipeline-total">
          <span>Active pipeline</span>
          <strong>
            {money(
              data.stages.find((stage) => stage.stage === "opportunity")
                ?.value ?? 0,
            )}
          </strong>
        </div>
      </header>
      {totalLeads === 0 && (
        <section className="panel workspace-empty">
          <Empty
            title="Pipeline is ready"
            text="Qualified leads will appear here as connected sources begin sending records."
          />
        </section>
      )}
      <section className="pipeline-track panel" data-tour="pipeline">
        {data.stages.map((stage, index) => (
          <button
            key={stage.stage}
            className={active === stage.stage ? "active" : ""}
            aria-pressed={active === stage.stage}
            onClick={() => setActive(stage.stage)}
          >
            <span>{String(index + 1).padStart(2, "0")}</span>
            <b>{stage.label}</b>
            <strong>{stage.count}</strong>
            <small>{stage.value ? money(stage.value) : "—"}</small>
            <span className="pipeline-lead-dots" aria-hidden="true">
              {stage.leads.slice(0, 4).map((lead) => (
                <i key={lead.id} />
              ))}
            </span>
            {index < data.stages.length - 1 && <i className="pipeline-link" />}
          </button>
        ))}
      </section>
      <section
        className="pipeline-inspector pipeline-detail-enter"
        key={selected.stage}
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
  );
}
