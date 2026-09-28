import { useEffect, useState } from "react";

import { deleteLead, getLead, listIntegrations, sendLeadToCrm, updateLeadTitle } from "../api";
import type { CrmSync, Lead } from "../types";

const title = (value: string) => value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
const crmProviderLabel = (provider: string) => provider === "hubspot" ? "HubSpot" : title(provider);

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

export function LeadDetail({ lead, canCrmSync = false, canEdit = false, onUpdated, onDeleted }: { lead: Lead; canCrmSync?: boolean; canEdit?: boolean; onUpdated?: (lead: Lead) => void; onDeleted?: (leadId: string) => void }) {
  const [sending, setSending] = useState(false);
  const [sendNotice, setSendNotice] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState(false);
  const [jobTitle, setJobTitle] = useState(lead.job_title || "");
  const [savingTitle, setSavingTitle] = useState(false);
  const [editNotice, setEditNotice] = useState<string | null>(null);
  const [crmResult, setCrmResult] = useState<CrmSync | null>(null);
  const [crmProviders, setCrmProviders] = useState<string[]>([]);
  const [selectedProvider, setSelectedProvider] = useState(lead.crm_sync?.provider || "");
  useEffect(() => {
    let active = true;
    listIntegrations().then((cards) => {
      if (!active) return;
      const providers = cards.filter((card) =>
        card.category.toLowerCase() === "crm" && card.directionality.manual_outbound &&
        card.connection?.is_active && card.connection.verification_status === "verified"
      ).map((card) => card.provider);
      setCrmProviders(providers);
      setSelectedProvider((current) =>
        providers.includes(lead.crm_sync?.provider || "") ? lead.crm_sync!.provider :
        providers.includes(current) ? current : providers.length === 1 ? providers[0] : ""
      );
    }).catch(() => { if (active) setCrmProviders([]); });
    return () => { active = false; };
  }, [lead.id, lead.crm_sync?.provider]);
  const analysis = lead.analysis;
  const qualification = lead.qualification;
  const route = lead.routing;
  const crm = crmResult?.lead_id === lead.id ? crmResult : lead.crm_sync;
  const icpRows = analysis ? [
    ["Industry", analysis.icp_evaluation.industry_fit],
    ["Company size", analysis.icp_evaluation.company_size_fit],
    ["Geography", analysis.icp_evaluation.geography_fit],
    ["Buyer role", analysis.icp_evaluation.buyer_role_fit],
    ["Budget", analysis.icp_evaluation.budget_fit],
    ["Pain relevance", analysis.icp_evaluation.pain_point_relevance],
  ] as const : [];

  return <article className="lead-story">
    <header className="story-head"><div><span className="eyebrow">Complete decision trail</span><h2>{lead.company_name}</h2><p>{lead.full_name} · {lead.job_title || "Role not supplied"}</p>{canEdit && (editingTitle ? <form onSubmit={async (event) => { event.preventDefault(); setSavingTitle(true); setEditNotice(null); try { const updated = await updateLeadTitle(lead.id, jobTitle); onUpdated?.(updated); setEditingTitle(false); setEditNotice("Title saved locally. No CRM was changed."); } catch (error) { setEditNotice(error instanceof Error ? error.message : "Unable to save title"); } finally { setSavingTitle(false); } }}><label>Job title <input aria-label="Job title" maxLength={255} required value={jobTitle} onChange={(event) => setJobTitle(event.target.value)} /></label><button className="secondary-button" type="submit" disabled={savingTitle}>{savingTitle ? "Saving…" : "Save title"}</button><button className="button-quiet" type="button" onClick={() => { setJobTitle(lead.job_title || ""); setEditingTitle(false); setEditNotice(null); }}>Cancel</button></form> : <button className="button-quiet" onClick={() => { setJobTitle(lead.job_title || ""); setEditingTitle(true); setEditNotice(null); }}>Edit title</button>)}{editNotice && <div className="notice" role="status">{editNotice}</div>}</div><div className="story-head-side">{qualification && <div className={`score-orb band-${qualification.classification.toLowerCase().replaceAll("_", "-")}`}><strong>{qualification.total_score}</strong><span>{title(qualification.classification)}</span></div>}{canEdit && <button className="danger-button" type="button" onClick={async () => { if (!window.confirm("Delete this lead from the workspace? A later matching inquiry can be imported again.")) return; try { await deleteLead(lead.id); onDeleted?.(lead.id); } catch (error) { setEditNotice(error instanceof Error ? error.message : "Unable to delete lead"); } }}>Delete lead</button>}</div></header>
    <LeadLifecycle lead={lead} />
    {route && <section className="story-section"><div className="story-index">→</div><div><span className="eyebrow">Follow-up strategy</span><h3>How to move this lead forward</h3><div className="follow-up-plan"><div className="follow-up-goal"><span>Conversion goal</span>{route.action_plan.conversion_goal || route.action_plan.next_step_recommendation}</div><h4>Recommended sequence</h4><ol>{(route.action_plan.follow_up_strategy?.length ? route.action_plan.follow_up_strategy : [route.action_plan.immediate_action, route.action_plan.next_step_recommendation]).map((step, index) => <li key={`${index}-${step}`}>{step}</li>)}</ol>{route.manual_review && <p className="attention-callout">Validate the analysis before contacting this lead.</p>}</div></div></section>}
    <section className="story-section"><div className="story-index">01</div><div><span className="eyebrow">Source / original inquiry</span><h3>{title(lead.source)} signal</h3><blockquote>{lead.message}</blockquote><div className="fact-grid"><Fact label="Received" value={new Date(lead.received_at).toLocaleString()} /><Fact label="Source detail" value={lead.source_detail || "Unspecified"} /><Fact label="External ID" value={lead.external_id || "Not supplied"} /></div></div></section>
    <section className="story-section"><div className="story-index">02</div><div><span className="eyebrow">Normalized lead</span><h3>Canonical commercial profile</h3><div className="fact-grid"><Fact label="Contact" value={`${lead.full_name} · ${lead.email}`} /><Fact label="Company" value={lead.company_name} /><Fact label="Industry" value={lead.industry || "Unknown"} /><Fact label="Geography" value={lead.geography || "Unknown"} /><Fact label="Team size" value={lead.employee_count ? `${lead.employee_count} people` : "Unknown"} /><Fact label="Requested service" value={lead.product_or_service} /></div></div></section>
    <section className="story-section"><div className="story-index">03</div><div><span className="eyebrow">AI context / intent</span><h3>{analysis ? `${title(analysis.intent_level)} intent · ${Math.round(analysis.overall_confidence * 100)}% confidence` : "Analysis unavailable"}</h3>{analysis && <><p className="story-summary">{analysis.summary.need} {analysis.summary.pain_point}</p><div className="signal-strip"><Signal name="Urgency" value={analysis.context.urgency.value} kind={analysis.context.urgency.information_kind} /><Signal name="Timeline" value={analysis.context.explicit_timeline.value} kind={analysis.context.explicit_timeline.information_kind} /><Signal name="Budget" value={analysis.context.budget_information.value} kind={analysis.context.budget_information.information_kind} /><Signal name="Buying stage" value={analysis.context.buying_stage.value} kind={analysis.context.buying_stage.information_kind} /></div>{analysis.requires_manual_review && <div className="attention-callout"><b>Human review required</b>{analysis.error_message || "Analysis confidence needs validation."}</div>}</>}</div></section>
    <section className="story-section"><div className="story-index">04</div><div><span className="eyebrow">ICP evaluation</span><h3>{analysis?.icp_evaluation.profile_name || "Profile unavailable"}</h3><div className="icp-matrix">{icpRows.map(([name, criterion]) => <div key={name}><i className={criterion.status} /><span>{name}</span><b>{title(criterion.status)}</b><small>{criterion.rationale}</small></div>)}</div></div></section>
    <section className="story-section"><div className="story-index">05</div><div><span className="eyebrow">Qualification</span><h3>{qualification ? `${title(qualification.classification)} · ${qualification.total_score}/100` : "Not qualified"}</h3>{qualification && <><div className="score-contract">{Object.entries(qualification.component_scores).map(([component, score]) => { const maximum = component === "icp_fit" ? 40 : component === "intent_urgency" ? 35 : 25; return <div key={component}><span>{title(component)}</span><b>{score}<small>/{maximum}</small></b><i><span style={{ transform: `scaleX(${score / maximum})` }} /></i></div>; })}</div><ul className="evidence-list">{qualification.rationale.map((reason) => <li key={reason}>{reason}</li>)}</ul></>}</div></section>
    <section className="story-section"><div className="story-index">06</div><div><span className="eyebrow">Routing / action plan</span><h3>{route ? `${title(route.assigned_queue)}${route.assigned_rep_name ? ` · ${route.assigned_rep_name}` : ""}` : "Routing pending"}</h3>{route && <><p className="story-summary">{route.routing_reason}</p><div className="action-command"><span>Recommended immediate action</span><b>{route.action_plan.immediate_action}</b><p>{route.action_plan.next_step_recommendation}</p></div><div className="split-list"><div><span className="eyebrow">Talking points</span>{route.action_plan.talking_points.map((item) => <p key={item}>→ {item}</p>)}</div><div><span className="eyebrow">Open questions</span>{route.action_plan.unanswered_questions.length ? route.action_plan.unanswered_questions.map((item) => <p key={item}>→ {item}</p>) : <p>No open qualification questions.</p>}</div></div></>}</div></section>
    <section className="story-section story-delivery"><div className="story-index">07</div><div><span className="eyebrow">Notification / CRM delivery</span><h3>External system state</h3><div className="delivery-grid"><div><span>Notification</span><b>{route ? title(route.notification_state) : "Pending"}</b><small>{route?.notifications[0]?.provider || "No provider event"}</small></div><div><span>CRM</span><b>{crm ? `${crmProviderLabel(crm.provider)} · ${title(crm.status)}` : "Not synchronized"}</b><small>{crm?.provider_entity_ids.contact_id || "No provider record ID"}</small></div></div>{canCrmSync && <div className="crm-action-controls">{crmProviders.length > 1 && <label>CRM provider <select aria-label="CRM provider" value={selectedProvider} onChange={(event) => setSelectedProvider(event.target.value)}><option value="">Select a CRM</option>{crmProviders.map((provider) => <option key={provider} value={provider}>{crmProviderLabel(provider)}</option>)}</select></label>}<button className="primary-button" disabled={sending || !selectedProvider} onClick={async () => { setSending(true); setSendNotice(null); try { const result = await sendLeadToCrm(lead.id, selectedProvider); setCrmResult(result); setSendNotice(result.status === "synced" ? `${crmProviderLabel(selectedProvider)} synchronized by your explicit request.` : `${crmProviderLabel(selectedProvider)} write ${title(result.status)}.`); try { onUpdated?.(await getLead(lead.id)); } catch { setSendNotice(`${crmProviderLabel(selectedProvider)} write ${title(result.status)}. Refresh lead details to see the latest state.`); } } catch (error) { setSendNotice(error instanceof Error ? error.message : "CRM write failed"); } finally { setSending(false); } }}>{sending ? "Sending…" : crm?.provider === selectedProvider && crm.status === "failed" ? `Retry ${crmProviderLabel(selectedProvider)}` : crm?.provider === selectedProvider && crm.status === "synced" ? `Update ${crmProviderLabel(selectedProvider)}` : `Send to ${crmProviderLabel(selectedProvider || "CRM")}`}</button></div>}{sendNotice && <div className="notice" role="status">{sendNotice}</div>}{crm?.last_error && <div className="attention-callout"><b>{crm.error_category || "CRM delivery issue"}</b>{crm.last_error}</div>}</div></section>
  </article>;
}

function Fact({ label, value }: { label: string; value: string }) { return <div className="fact"><span>{label}</span><b>{value}</b></div>; }
function Signal({ name, value, kind }: { name: string; value: string | null; kind: string }) { return <div className={`signal ${kind}`}><span>{name} · {kind}</span><b>{value || "Unknown"}</b></div>; }
