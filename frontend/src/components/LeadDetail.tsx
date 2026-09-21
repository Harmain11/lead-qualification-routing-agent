import type { Lead } from "../types";

const title = (value: string) => value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());

export function LeadLifecycle({ lead }: { lead: Lead }) {
  const stages = [
    ["Intake", true],
    ["Normalize", true],
    ["Analyze", Boolean(lead.analysis)],
    ["Qualify", Boolean(lead.qualification)],
    ["Route", Boolean(lead.routing)],
    ["Notify", Boolean(lead.routing?.notification_state)],
    ["CRM", Boolean(lead.crm_sync)],
  ] as const;
  return <div className="detail-lifecycle" aria-label="Lead lifecycle">{stages.map(([stage, complete], index) => <div className={complete ? "complete" : "waiting"} key={stage}><i>{complete ? "✓" : index + 1}</i><span>{stage}</span></div>)}</div>;
}

export function LeadDetail({ lead }: { lead: Lead }) {
  const analysis = lead.analysis;
  const qualification = lead.qualification;
  const route = lead.routing;
  const crm = lead.crm_sync;
  const icpRows = analysis ? [
    ["Industry", analysis.icp_evaluation.industry_fit],
    ["Company size", analysis.icp_evaluation.company_size_fit],
    ["Geography", analysis.icp_evaluation.geography_fit],
    ["Buyer role", analysis.icp_evaluation.buyer_role_fit],
    ["Budget", analysis.icp_evaluation.budget_fit],
    ["Pain relevance", analysis.icp_evaluation.pain_point_relevance],
  ] as const : [];

  return <article className="lead-story">
    <header className="story-head"><div><span className="eyebrow">Complete decision trail</span><h2>{lead.company_name}</h2><p>{lead.full_name} · {lead.job_title || "Role not supplied"}</p></div>{qualification && <div className={`score-orb band-${qualification.classification.toLowerCase().replaceAll("_", "-")}`}><strong>{qualification.total_score}</strong><span>{title(qualification.classification)}</span></div>}</header>
    <LeadLifecycle lead={lead} />
    <section className="story-section"><div className="story-index">01</div><div><span className="eyebrow">Source / original inquiry</span><h3>{title(lead.source)} signal</h3><blockquote>{lead.message}</blockquote><div className="fact-grid"><Fact label="Received" value={new Date(lead.received_at).toLocaleString()} /><Fact label="Source detail" value={lead.source_detail || "Unspecified"} /><Fact label="External ID" value={lead.external_id || "Not supplied"} /></div></div></section>
    <section className="story-section"><div className="story-index">02</div><div><span className="eyebrow">Normalized lead</span><h3>Canonical commercial profile</h3><div className="fact-grid"><Fact label="Contact" value={`${lead.full_name} · ${lead.email}`} /><Fact label="Company" value={lead.company_name} /><Fact label="Industry" value={lead.industry || "Unknown"} /><Fact label="Geography" value={lead.geography || "Unknown"} /><Fact label="Team size" value={lead.employee_count ? `${lead.employee_count} people` : "Unknown"} /><Fact label="Requested service" value={lead.product_or_service} /></div></div></section>
    <section className="story-section"><div className="story-index">03</div><div><span className="eyebrow">AI context / intent</span><h3>{analysis ? `${title(analysis.intent_level)} intent · ${Math.round(analysis.overall_confidence * 100)}% confidence` : "Analysis unavailable"}</h3>{analysis && <><p className="story-summary">{analysis.summary.need} {analysis.summary.pain_point}</p><div className="signal-strip"><Signal name="Urgency" value={analysis.context.urgency.value} kind={analysis.context.urgency.information_kind} /><Signal name="Timeline" value={analysis.context.explicit_timeline.value} kind={analysis.context.explicit_timeline.information_kind} /><Signal name="Budget" value={analysis.context.budget_information.value} kind={analysis.context.budget_information.information_kind} /><Signal name="Buying stage" value={analysis.context.buying_stage.value} kind={analysis.context.buying_stage.information_kind} /></div>{analysis.requires_manual_review && <div className="attention-callout"><b>Human review required</b>{analysis.error_message || "Analysis confidence needs validation."}</div>}</>}</div></section>
    <section className="story-section"><div className="story-index">04</div><div><span className="eyebrow">ICP evaluation</span><h3>{analysis?.icp_evaluation.profile_name || "Profile unavailable"}</h3><div className="icp-matrix">{icpRows.map(([name, criterion]) => <div key={name}><i className={criterion.status} /><span>{name}</span><b>{title(criterion.status)}</b><small>{criterion.rationale}</small></div>)}</div></div></section>
    <section className="story-section"><div className="story-index">05</div><div><span className="eyebrow">Qualification</span><h3>{qualification ? `${title(qualification.classification)} · ${qualification.total_score}/100` : "Not qualified"}</h3>{qualification && <><div className="score-contract">{Object.entries(qualification.component_scores).map(([component, score]) => { const maximum = component === "icp_fit" ? 40 : component === "intent_urgency" ? 35 : 25; return <div key={component}><span>{title(component)}</span><b>{score}<small>/{maximum}</small></b><i><span style={{ transform: `scaleX(${score / maximum})` }} /></i></div>; })}</div><ul className="evidence-list">{qualification.rationale.map((reason) => <li key={reason}>{reason}</li>)}</ul></>}</div></section>
    <section className="story-section"><div className="story-index">06</div><div><span className="eyebrow">Routing / action plan</span><h3>{route ? `${title(route.assigned_queue)}${route.assigned_rep_name ? ` · ${route.assigned_rep_name}` : ""}` : "Routing pending"}</h3>{route && <><p className="story-summary">{route.routing_reason}</p><div className="action-command"><span>Recommended immediate action</span><b>{route.action_plan.immediate_action}</b><p>{route.action_plan.next_step_recommendation}</p></div><div className="split-list"><div><span className="eyebrow">Talking points</span>{route.action_plan.talking_points.map((item) => <p key={item}>→ {item}</p>)}</div><div><span className="eyebrow">Open questions</span>{route.action_plan.unanswered_questions.length ? route.action_plan.unanswered_questions.map((item) => <p key={item}>→ {item}</p>) : <p>No open qualification questions.</p>}</div></div></>}</div></section>
    <section className="story-section story-delivery"><div className="story-index">07</div><div><span className="eyebrow">Notification / CRM delivery</span><h3>External system state</h3><div className="delivery-grid"><div><span>Notification</span><b>{route ? title(route.notification_state) : "Pending"}</b><small>{route?.notifications[0]?.provider || "No provider event"}</small></div><div><span>CRM</span><b>{crm ? `${title(crm.provider)} · ${title(crm.status)}` : "Not synchronized"}</b><small>{crm?.provider_entity_ids.contact_id || "No provider record ID"}</small></div></div>{crm?.last_error && <div className="attention-callout"><b>{crm.error_category || "CRM delivery issue"}</b>{crm.last_error}</div>}</div></section>
  </article>;
}

function Fact({ label, value }: { label: string; value: string }) { return <div className="fact"><span>{label}</span><b>{value}</b></div>; }
function Signal({ name, value, kind }: { name: string; value: string | null; kind: string }) { return <div className={`signal ${kind}`}><span>{name} · {kind}</span><b>{value || "Unknown"}</b></div>; }
