import type { AgentActivity, PipelineAnalytics } from "../types";

const nodes = [
  ["Sources", "website · Gmail · CRM inbound"],
  ["Normalize", "canonical lead"],
  ["AI Context", "intent + evidence"],
  ["ICP", "grounded fit"],
  ["Qualify", "40 / 35 / 25"],
  ["Route", "queue + rep"],
  ["Actions", "next-best step"],
  ["CRM + Notify", "durable delivery"],
] as const;

export function AgentFlow({
  pipeline,
  activity,
}: {
  pipeline: PipelineAnalytics | null;
  activity: AgentActivity | null;
}) {
  const eventStages = new Set(activity?.activity.map((item) => item.stage) ?? []);
  const received = pipeline?.stages.find((stage) => stage.stage === "received")?.count ?? 0;
  const activeIndex = received === 0 ? 0 : eventStages.has("crm") ? 7 : eventStages.has("routed") ? 6 : 4;

  return (
    <section className="agent-flow panel" aria-labelledby="agent-flow-title">
      <div className="section-heading">
        <div>
          <span className="eyebrow">Live operating model</span>
          <h2 id="agent-flow-title">Signal-to-revenue intelligence path</h2>
        </div>
        <span className="flow-status"><i /> {received} signals in range</span>
      </div>
      <div className="flow-viewport">
        <div className="flow-plane" role="list" aria-label="Operational lead journey">
          {nodes.map(([name, detail], index) => (
            <div
              className={`flow-node ${index <= activeIndex ? "complete" : "waiting"} ${index === activeIndex ? "active" : ""}`}
              key={name}
              role="listitem"
            >
              <span className="flow-index">{String(index + 1).padStart(2, "0")}</span>
              <div><b>{name}</b><small>{detail}</small></div>
              {index < nodes.length - 1 && <i className="flow-link" aria-hidden="true"><span /></i>}
            </div>
          ))}
        </div>
      </div>
      <div className="flow-legend">
        <span><i className="implemented" /> Implemented capability</span>
        <span><i className="future" /> Future agents remain inactive</span>
        <span>Motion reflects pipeline state, not model reasoning.</span>
      </div>
    </section>
  );
}
