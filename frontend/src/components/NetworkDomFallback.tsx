import { useState } from "react";

import type { AgentActivity, Lead, PipelineAnalytics } from "../types";
import { fullActiveStages, latestActivityLabel, stageById, stageFacts, stages } from "./networkData";

export function NetworkDomFallback({ pipeline, activity, lead }: { pipeline: PipelineAnalytics; activity: AgentActivity; lead: Lead | null }) {
  const [selected, setSelected] = useState(stageById.get("qualification")!);
  const active = fullActiveStages(lead);
  const received = pipeline.stages.find((stage) => stage.stage === "received")?.count ?? 0;

  return <section className="intelligence-network panel" aria-labelledby="network-title">
    <img className="network-twinkle" src="/asset/brand/twinkle-widecard.svg" alt="" aria-hidden="true" />
    <div className="section-heading network-heading">
      <div><span className="eyebrow">Live intelligence topology</span><h2 id="network-title">AI lead intelligence network</h2></div>
      <div className="network-live-state"><span className="flow-status"><i /> {received} signals in range</span><small>{latestActivityLabel(activity)}</small></div>
    </div>
    <div className="network-layout">
      <div className="network-stage">
        <div className="network-fallback" role="group" aria-label="Lead intelligence network lightweight view">
          {stages.map((stage) => <button type="button" className={`${active.has(stage.id) ? "active" : ""} ${selected.id === stage.id ? "selected" : ""}`} onClick={() => setSelected(stage)} onFocus={() => setSelected(stage)} key={stage.id}><small>{stage.lane}</small>{stage.label}</button>)}
        </div>
        <div className="network-lanes" aria-hidden="true"><span>Inbound</span><span>AI processing</span><span>Decision</span><span>Destinations</span></div>
      </div>
      <NetworkStageInspector selected={selected} lead={lead} active={active.has(selected.id)} />
    </div>
    <div className="network-accessible" aria-label="Inspect network stage">{stages.map((stage) => <button className={selected.id === stage.id ? "active" : ""} onClick={() => setSelected(stage)} onFocus={() => setSelected(stage)} key={stage.id}>{stage.label}</button>)}</div>
  </section>;
}

export function NetworkStageInspector({ selected, lead, active }: { selected: (typeof stages)[number]; lead: Lead | null; active: boolean }) {
  return <aside className="network-inspector" aria-live="polite">
    <div className="network-inspector-state"><i className={active ? "active" : ""} /><span>{active ? "Stage complete" : "No current event"}</span></div>
    <div className="network-inspector-content" key={`${selected.id}-${lead?.id ?? "none"}`}>
      <span className="eyebrow">{selected.lane}</span><h3>{selected.label}</h3><p>{selected.detail}</p>
      <dl>{stageFacts(selected, lead).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    </div>
  </aside>;
}
