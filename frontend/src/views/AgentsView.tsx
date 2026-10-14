import { AgentFlow } from "../components/AgentFlow";
import type { AgentActivity, PipelineAnalytics } from "../types";
import { Panel, Unavailable } from "./productShared";

export function AgentsView({
  data,
  pipeline,
}: {
  data: AgentActivity | null;
  pipeline: PipelineAnalytics | null;
}) {
  if (!data) return <Unavailable />;
  return (
    <div className="page-view">
      <header className="page-hero compact-hero">
        <div>
          <span className="eyebrow">Agentic operations center</span>
          <h1>AI activity</h1>
          <p>
            Business-facing results, tool invocations and audit states—without
            exposing hidden model reasoning.
          </p>
        </div>
        <div className="agent-count">
          <strong>
            {
              data.capabilities.filter((item) => item.status === "implemented")
                .length
            }
          </strong>
          <span>implemented capabilities</span>
        </div>
      </header>
      <AgentFlow pipeline={pipeline} activity={data} />
      <Panel
        title="Active agent insights"
        eyebrow="Read-only · selected date range"
      >
        <div className="agent-insight-grid">
          {data.insights.map((insight) => (
            <article className="agent-insight-card" key={insight.agent}>
              <header className="agent-insight-card-header">
                <span className="eyebrow">{insight.agent}</span>
                <span className="agent-insight-mode">Read-only</span>
              </header>
              <p>{insight.summary}</p>
              <dl className="agent-insight-metrics">
                {insight.metrics.map((metric) => (
                  <div key={metric.key}>
                    <dt>{metric.label}</dt>
                    <dd>{metric.value}</dd>
                  </div>
                ))}
              </dl>
              {insight.details.length > 0 && (
                <ul>
                  {insight.details.map((detail) => (
                    <li key={detail}>{detail}</li>
                  ))}
                </ul>
              )}
            </article>
          ))}
        </div>
      </Panel>
    </div>
  );
}
