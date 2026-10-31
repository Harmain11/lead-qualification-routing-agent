import { OperationsDialog } from "../components/OperationsDialog";
import { useCallback,useEffect,useMemo,useRef,useState } from "react";

import {
createIcpDraft,
getCurrentIcp,
getSystemDefaultIcp,
listIcpVersions,
previewIcpDraft,
publishIcpDraft,
updateIcpDraft,
validateIcpDraft,
} from "../api";
import type { IcpConfiguration,IcpPreview,IcpValidation,IcpVersion } from "../types";
import { parseCommaSeparatedList } from "../textEntry";
import { PageHeader } from "../v3/ui/primitives";

const SECTIONS = [
  "Company Profile",
  "Buyer Profile",
  "Commercial Fit",
  "Technology / Requirements",
  "Exclusions / Hard Disqualifiers",
  "Scoring Weights",
  "Review & Test",
] as const;

const CRITERIA: Array<[string, string]> = [
  ["industry_match", "Industry match"],
  ["company_size_fit", "Company size"],
  ["geography_match", "Geography"],
  ["buyer_role_match", "Buyer role"],
  ["decision_maker", "Decision maker"],
  ["budget_fit", "Budget fit"],
  ["revenue_fit", "Annual revenue"],
  ["business_type_match", "Business type"],
  ["strategic_account", "Strategic account"],
  ["technology_fit", "Technology fit"],
  ["product_interest", "Product interest"],
  ["purchasing_timeline", "Purchasing timeline"],
  ["department_match", "Department / function"],
  ["seniority_match", "Seniority"],
  ["required_signals", "Required signals"],
  ["preferred_signals", "Preferred signals"],
];

const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T;
const csv = (values: string[]) => values.join(", ");
const pretty = (value: string) => value.replaceAll("_", " ").replace(/\b\w/g, (item) => item.toUpperCase());

export function IcpView({ canManage, onBack }: { canManage: boolean; onBack: () => void }) {
  const [current, setCurrent] = useState<IcpVersion | null>(null);
  const [systemDefault, setSystemDefault] = useState<IcpVersion | null>(null);
  const [versions, setVersions] = useState<IcpVersion[]>([]);
  const [selected, setSelected] = useState<IcpVersion | null>(null);
  const [configuration, setConfiguration] = useState<IcpConfiguration | null>(null);
  const [section, setSection] = useState(0);
  const [validation, setValidation] = useState<IcpValidation | null>(null);
  const [preview, setPreview] = useState<IcpPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [publishOpen, setPublishOpen] = useState(false);
  const [confirmUse, setConfirmUse] = useState(false);
  const [confirmHistory, setConfirmHistory] = useState(false);
  const [confirmExclusions, setConfirmExclusions] = useState(false);

  const load = useCallback(async (preferId?: string | null) => {
    const [active, history, defaultIcp] = await Promise.all([
      getCurrentIcp(),
      listIcpVersions(),
      getSystemDefaultIcp(),
    ]);
    setCurrent(active);
    setSystemDefault(defaultIcp);
    setVersions(history);
    const target = history.find((item) => item.id === preferId)
      ?? history.find((item) => item.status === "draft")
      ?? active;
    setSelected(target);
    setConfiguration(clone(target.configuration));
  }, []);

  useEffect(() => {
    setBusy(true);
    load().catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Unable to load ICP"))
      .finally(() => setBusy(false));
  }, [load]);

  const editable = Boolean(canManage && selected?.editable && selected.id);
  const points = configuration?.scoring.component_points.icp_fit ?? {};
  const total = Object.values(points).reduce((sum, value) => sum + Number(value || 0), 0);
  const exclusionsChanged = useMemo(() => {
    if (!current || !configuration) return false;
    return JSON.stringify(current.configuration.disqualifiers) !== JSON.stringify(configuration.disqualifiers);
  }, [configuration, current]);

  function choose(version: IcpVersion) {
    setSelected(version);
    setConfiguration(clone(version.configuration));
    setValidation(null);
    setPreview(null);
    setNotice(null);
    setSection(0);
  }

  function updateConfig(mutator: (next: IcpConfiguration) => void) {
    if (!configuration || !editable) return;
    const next = clone(configuration);
    mutator(next);
    setConfiguration(next);
    setValidation(null);
    setPreview(null);
  }

  async function createDraft() {
    setBusy(true); setError(null);
    try {
      const draft = await createIcpDraft(current?.id ? { based_on_version_id: current.id } : {});
      await load(draft.id);
      setNotice(
        `Custom draft v${draft.version_number} created from the ${
          current?.source === "workspace" ? "current workspace ICP" : "built-in template"
        }. It will not affect scoring until published.`,
      );
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to create draft"); }
    finally { setBusy(false); }
  }

  async function openCustom() {
    const draft = versions.find((version) => version.status === "draft");
    if (draft) {
      choose(draft);
      return;
    }
    if (!canManage) {
      const customVersion = current?.source === "workspace"
        ? current
        : versions.find((version) => version.status === "published") ?? versions[0];
      if (customVersion) choose(customVersion);
      return;
    }
    if (selected?.source === "workspace" && selected.editable) return;
    await createDraft();
  }

  async function saveDraft() {
    if (!selected?.id || !configuration) return;
    setBusy(true); setError(null);
    try {
      const saved = await updateIcpDraft(selected.id, {
        name: selected.name,
        description: selected.description,
        configuration,
      });
      await load(saved.id);
      setNotice("Draft saved. Published scoring remains unchanged.");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to save draft"); }
    finally { setBusy(false); }
  }

  async function runValidation() {
    if (!selected?.id) return;
    await saveDraft();
    setBusy(true); setError(null);
    try {
      const result = await validateIcpDraft(selected.id);
      setValidation(result);
      setNotice(result.valid ? "Draft is valid and ready to test." : null);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Validation failed"); }
    finally { setBusy(false); }
  }

  async function runPreview() {
    if (!selected?.id) return;
    await saveDraft();
    setBusy(true); setError(null);
    try { setPreview(await previewIcpDraft(selected.id)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Preview failed"); }
    finally { setBusy(false); }
  }

  async function publish() {
    if (!selected?.id || !confirmUse || !confirmHistory || (exclusionsChanged && !confirmExclusions)) return;
    setBusy(true); setError(null);
    try {
      const result = await publishIcpDraft(selected.id, confirmExclusions);
      await load(result.id);
      setPublishOpen(false);
      setNotice(`ICP v${result.version_number} is now published. Historical results were preserved.`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Publication failed"); }
    finally { setBusy(false); }
  }

  if (busy && !configuration) return <div className="icp-loading">Loading workspace ICP…</div>;
  if (!configuration || !selected) return <div className="icp-loading">{error || "ICP is unavailable."}</div>;

  return <div className="page-view operational-page icp-page">
    <header className="icp-hero-v3">
      <PageHeader back={{ label: "Settings", onClick: onBack }} title="Ideal customer profile" subtitle="Describe who you sell to, test it against existing leads, then publish. Corral scores every new lead’s fit out of 40 points." />
      <div className="icp-current-card"><small>Live profile</small>{current?.source === "not_configured" ? <><strong>None published yet</strong><span>Fit scoring and hard exclusions are off until you publish one.</span></> : <><strong>{current?.name}</strong><span>{current?.source === "system_default" ? "Built-in template" : `Workspace v${current?.version_number}`} · {current?.status}</span><span>Last updated {current?.updated_at ? new Date(current.updated_at).toLocaleString() : "by system policy"}</span></>}</div>
    </header>
    <div className="icp-toolbar">
      <span className={`icp-status status-${selected.status}`}>{selected.source === "not_configured" ? "Not configured" : `${selected.source === "system_default" ? "Built-in template" : `v${selected.version_number}`} · ${pretty(selected.status)}`}</span>
      <span className={`weight-pill ${total === 40 ? "valid" : "invalid"}`} aria-label={`${total} of 40 ICP points configured`}><b>{total} / 40</b> ICP points · {total === 40 ? "valid" : "needs attention"}</span>
      {editable && <button className="secondary-button" disabled={busy} onClick={() => void saveDraft()}>Save draft</button>}
      {canManage && !editable && <button className="primary-button" disabled={busy} onClick={() => void createDraft()}>Create new draft</button>}
    </div>
     <section className="icp-mode-picker" aria-label="Choose ICP setup mode">
       <div className="icp-mode-intro">
         <span className="eyebrow">ICP setup</span>
          <p>The built-in profile is only a template. A published custom ICP is required for fit scoring.</p>
       </div>
       <div className="icp-mode-options">
         <button
           type="button"
           className={`icp-mode-option ${selected.source === "system_default" ? "active" : ""}`}
           aria-pressed={selected.source === "system_default"}
           disabled={busy || !systemDefault}
           onClick={() => systemDefault && choose(systemDefault)}
         >
            <span><b>Built-in template</b><small>View an example profile; it is not used to score leads.</small></span>
         </button>
         <button
           type="button"
           className={`icp-mode-option ${selected.source === "workspace" ? "active" : ""}`}
           aria-pressed={selected.source === "workspace"}
           disabled={busy || (!canManage && versions.length === 0)}
           onClick={() => void openCustom()}
         >
            <span><b>Custom</b><small>Publish a workspace ICP to enable fit scoring and hard exclusions.</small></span>
         </button>
       </div>
     </section>
    {error && <div className="team-alert" role="alert"><span>{error}</span><button onClick={() => setError(null)} aria-label="Dismiss message">×</button></div>}
    {notice && <div className="icp-notice" role="status">{notice}</div>}
     <div className="icp-layout">
      <aside className="panel icp-sections" aria-label="ICP builder sections">{SECTIONS.map((label, index) => <button key={label} className={section === index ? "active" : ""} onClick={() => setSection(index)}><i>{index + 1}</i><span>{label}</span></button>)}</aside>
     <section className="panel icp-builder" data-tour="icp">
        <div className="icp-builder-head"><div><span className="eyebrow">Step {section + 1} of 7</span><h2>{SECTIONS[section]}</h2></div>{!editable && <span className="read-only-badge">Read only</span>}</div>
        {section === 0 && <div className="icp-profile-meta"><label className="icp-field"><span>ICP name</span><input disabled={!editable} value={selected.name} onChange={(event) => { const name = event.target.value; setSelected((value) => value ? { ...value, name } : value); updateConfig((next) => { next.profile_name = name; }); }} /></label><label className="icp-field"><span>Description</span><input disabled={!editable} value={selected.description} onChange={(event) => { const description = event.target.value; setSelected((value) => value ? { ...value, description } : value); }} placeholder="Explain who this ICP is designed to identify" /></label></div>}
        {section === 0 && <CompanySection config={configuration} editable={editable} update={updateConfig} />}
        {section === 1 && <BuyerSection config={configuration} editable={editable} update={updateConfig} />}
        {section === 2 && <CommercialSection config={configuration} editable={editable} update={updateConfig} />}
        {section === 3 && <TechnologySection config={configuration} editable={editable} update={updateConfig} />}
        {section === 4 && <ExclusionsSection config={configuration} editable={editable} update={updateConfig} />}
        {section === 5 && <WeightsSection config={configuration} editable={editable} update={updateConfig} total={total} />}
        {section === 6 && <ReviewSection selected={selected} total={total} validation={validation} preview={preview} editable={editable} busy={busy} runValidation={runValidation} runPreview={runPreview} openPublish={() => setPublishOpen(true)} />}
        <div className="icp-step-actions"><button className="button-quiet" disabled={section === 0} onClick={() => setSection((value) => Math.max(0, value - 1))}>Previous</button><button className="secondary-button" disabled={section === 6} onClick={() => setSection((value) => Math.min(6, value + 1))}>Next section ›</button></div>
      </section>
        <aside className="panel icp-history"><div className="icp-history-head"><span className="eyebrow">Version history</span><h2>Workspace versions</h2></div>{systemDefault && <button className={selected.source === "system_default" ? "active" : ""} onClick={() => choose(systemDefault)}><b>Built-in template</b><span>Not used for lead qualification</span><em>Template</em></button>}{versions.map((version) => <button key={version.id} className={selected.id === version.id ? "active" : ""} onClick={() => choose(version)}><b>v{version.version_number} · {version.name}</b><span>{version.created_by || "Unknown author"}</span><span>{version.updated_at ? new Date(version.updated_at).toLocaleString() : "—"}</span><em>{pretty(version.status)}</em></button>)}</aside>
    </div>
    {publishOpen && <OperationsDialog title="Publish workspace ICP?" className="team-modal confirm-modal icp-publish-modal" showHeader={false} onClose={() => { if (!busy) setPublishOpen(false); }}><button className="modal-close" onClick={() => setPublishOpen(false)} aria-label="Close">×</button><span className="eyebrow">Deliberate policy change</span><h2 id="publish-title">Publish workspace ICP?</h2><p>New qualification runs will use this version. Existing lead scores and their ICP references will not be rewritten.</p><label><input type="checkbox" checked={confirmUse} onChange={(event) => setConfirmUse(event.target.checked)} /> I understand new leads will use this version.</label><label><input type="checkbox" checked={confirmHistory} onChange={(event) => setConfirmHistory(event.target.checked)} /> I understand historical results are preserved.</label>{exclusionsChanged && <label className="exclusion-confirm"><input type="checkbox" checked={confirmExclusions} onChange={(event) => setConfirmExclusions(event.target.checked)} /> I confirm the hard-disqualifier changes may force matching leads to Disqualified.</label>}<div className="team-modal-actions"><button className="button-quiet" onClick={() => setPublishOpen(false)}>Cancel</button><button className="primary-button" disabled={total !== 40 || !confirmUse || !confirmHistory || (exclusionsChanged && !confirmExclusions) || busy} onClick={() => void publish()}>Publish ICP</button></div></OperationsDialog>}
  </div>;
}

function TextList({ label, value, disabled, onChange, hint }: { label: string; value: string[]; disabled: boolean; onChange: (items: string[]) => void; hint?: string }) {
  const [draft, setDraft] = useState(() => csv(value));
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (document.activeElement !== inputRef.current) setDraft(csv(value));
  }, [value]);
  return <label className="icp-field"><span>{label}</span><input ref={inputRef} value={draft} disabled={disabled} onChange={(event) => setDraft(event.target.value)} onBlur={() => { const normalized = parseCommaSeparatedList(draft); onChange(normalized); setDraft(csv(normalized)); }} placeholder="Enter values separated by commas" />{hint && <small>{hint}</small>}</label>;
}

function NumberField({ label, value, disabled, onChange }: { label: string; value: number; disabled: boolean; onChange: (value: number) => void }) {
  return <label className="icp-field"><span>{label}</span><input type="number" min="0" value={value} disabled={disabled} onChange={(event) => onChange(Math.max(0, Number(event.target.value)))} /></label>;
}

type SectionProps = { config: IcpConfiguration; editable: boolean; update: (fn: (next: IcpConfiguration) => void) => void };

function CompanySection({ config, editable, update }: SectionProps) {
  return <div className="icp-form-grid"><TextList label="Target industries" value={config.target_industries} disabled={!editable} onChange={(value) => update((next) => { next.target_industries = value; })} /><TextList label="Excluded industries" value={config.excluded_industries} disabled={!editable} onChange={(value) => update((next) => { next.excluded_industries = value; })} hint="Excluded industries are deterministic hard disqualifiers." /><TextList label="Countries / regions" value={config.target_geographies} disabled={!editable} onChange={(value) => update((next) => { next.target_geographies = value; })} /><TextList label="Business types" value={config.target_business_types} disabled={!editable} onChange={(value) => update((next) => { next.target_business_types = value; })} /><TextList label="Strategic accounts" value={config.strategic_accounts} disabled={!editable} onChange={(value) => update((next) => { next.strategic_accounts = value; })} /><div className="icp-range"><NumberField label="Employees minimum" value={config.company_size.employees_min} disabled={!editable} onChange={(value) => update((next) => { next.company_size.employees_min = value; })} /><NumberField label="Employees maximum" value={config.company_size.employees_max} disabled={!editable} onChange={(value) => update((next) => { next.company_size.employees_max = value; })} /></div><div className="icp-range"><NumberField label="Annual revenue minimum" value={config.company_size.annual_revenue_min} disabled={!editable} onChange={(value) => update((next) => { next.company_size.annual_revenue_min = value; })} /><NumberField label="Annual revenue maximum" value={config.company_size.annual_revenue_max} disabled={!editable} onChange={(value) => update((next) => { next.company_size.annual_revenue_max = value; })} /></div></div>;
}

function BuyerSection({ config, editable, update }: SectionProps) {
  return <div className="icp-form-grid"><TextList label="Decision-maker titles" value={config.buyer_roles.decision_makers ?? []} disabled={!editable} onChange={(value) => update((next) => { next.buyer_roles.decision_makers = value; })} /><TextList label="Influencer titles" value={config.buyer_roles.influencers ?? []} disabled={!editable} onChange={(value) => update((next) => { next.buyer_roles.influencers = value; })} /><TextList label="Additional job titles" value={config.buyer.job_titles} disabled={!editable} onChange={(value) => update((next) => { next.buyer.job_titles = value; })} /><TextList label="Seniority indicators" value={config.buyer.seniorities} disabled={!editable} onChange={(value) => update((next) => { next.buyer.seniorities = value; })} /><TextList label="Departments / functions" value={config.buyer.departments} disabled={!editable} onChange={(value) => update((next) => { next.buyer.departments = value; })} /><TextList label="Decision-maker indicators" value={config.buyer.decision_maker_indicators} disabled={!editable} onChange={(value) => update((next) => { next.buyer.decision_maker_indicators = value; })} /><TextList label="Excluded personas" value={config.buyer.excluded_personas} disabled={!editable} onChange={(value) => update((next) => { next.buyer.excluded_personas = value; })} /></div>;
}

function CommercialSection({ config, editable, update }: SectionProps) {
  return <div className="icp-form-grid"><div className="icp-range"><NumberField label="Minimum budget" value={config.budget.minimum} disabled={!editable} onChange={(value) => update((next) => { next.budget.minimum = value; })} /><NumberField label="Preferred budget" value={config.budget.typical} disabled={!editable} onChange={(value) => update((next) => { next.budget.typical = value; })} /></div><TextList label="Service / product interests" value={config.commercial.service_interests} disabled={!editable} onChange={(value) => update((next) => { next.commercial.service_interests = value; })} /><TextList label="Purchasing timeline signals" value={config.commercial.purchasing_timelines} disabled={!editable} onChange={(value) => update((next) => { next.commercial.purchasing_timelines = value; })} /><TextList label="Relevant pain points" value={config.pain_points} disabled={!editable} onChange={(value) => update((next) => { next.pain_points = value; })} /></div>;
}

function TechnologySection({ config, editable, update }: SectionProps) {
  return <div className="icp-form-grid"><TextList label="Technology stack indicators" value={config.technology.technology_stack} disabled={!editable} onChange={(value) => update((next) => { next.technology.technology_stack = value; })} /><TextList label="Platform / tool requirements" value={config.technology.platform_requirements} disabled={!editable} onChange={(value) => update((next) => { next.technology.platform_requirements = value; })} /><TextList label="Required signals" value={config.fit_signals.required} disabled={!editable} onChange={(value) => update((next) => { next.fit_signals.required = value; })} hint="Required-signal points are awarded only when all listed signals are observed." /><TextList label="Preferred signals" value={config.fit_signals.preferred} disabled={!editable} onChange={(value) => update((next) => { next.fit_signals.preferred = value; })} /><TextList label="Negative signals" value={config.fit_signals.exclusions} disabled={!editable} onChange={(value) => update((next) => { next.fit_signals.exclusions = value; })} /></div>;
}

function ExclusionsSection({ config, editable, update }: SectionProps) {
  return <div><div className="exclusion-explainer"><b>Hard disqualifiers override positive fit.</b><p>When a configured rule matches, deterministic code sets the lead to Disqualified and records the rule, evidence and ICP version.</p></div><div className="disqualifier-list">{config.disqualifiers.map((rule, index) => <article key={`${rule.code}-${index}`}><div><label className="icp-field"><span>Rule code</span><input disabled={!editable} value={rule.code} onChange={(event) => update((next) => { next.disqualifiers[index].code = event.target.value; })} /></label><label className="icp-field"><span>Match field</span><select disabled={!editable} value={rule.field} onChange={(event) => update((next) => { next.disqualifiers[index].field = event.target.value as typeof rule.field; })}><option value="searchable">All lead text</option><option value="industry">Industry</option><option value="geography">Geography</option><option value="job_title">Job title</option><option value="email">Email</option><option value="company_name">Company name</option><option value="product_or_service">Product / service</option></select></label></div><label className="icp-field"><span>Description</span><input disabled={!editable} value={rule.description} onChange={(event) => update((next) => { next.disqualifiers[index].description = event.target.value; })} /></label><TextList label="Matches any of" value={[...rule.keywords, ...rule.values]} disabled={!editable} onChange={(value) => update((next) => { next.disqualifiers[index].keywords = value; next.disqualifiers[index].values = []; })} />{editable && <button className="danger-button" onClick={() => update((next) => { next.disqualifiers.splice(index, 1); })}>Remove rule</button>}</article>)}</div>{editable && <button className="secondary-button" onClick={() => update((next) => { next.disqualifiers.push({ code: `rule_${next.disqualifiers.length + 1}`, description: "", severity: "hard", field: "searchable", operator: "contains_any", keywords: [], values: [] }); })}>+ Add hard disqualifier</button>}</div>;
}

function WeightsSection({ config, editable, update, total }: SectionProps & { total: number }) {
  return <div><div className={`weight-total ${total === 40 ? "valid" : "invalid"}`}><span>ICP POINTS</span><strong>{total} / 40</strong><p>{total === 40 ? "Valid distribution. Intent remains 35 and Engagement remains 25." : `Adjust the criteria by ${Math.abs(40 - total)} point${Math.abs(40 - total) === 1 ? "" : "s"}. Publishing requires exactly 40.`}</p></div><div className="weight-list"><div className="weight-list-head"><span>Criterion</span><span>Rule</span><span>Maximum points</span></div>{CRITERIA.map(([key, label]) => <div key={key}><span><b>{label}</b><small>Unknown data earns 0 points</small></span><span>deterministic match</span><input aria-label={`${label} maximum points`} type="number" min="0" max="40" disabled={!editable} value={config.scoring.component_points.icp_fit[key] ?? 0} onChange={(event) => update((next) => { next.scoring.component_points.icp_fit[key] = Math.max(0, Math.min(40, Number(event.target.value))); })} /></div>)}</div></div>;
}

function ReviewSection({ selected, total, validation, preview, editable, busy, runValidation, runPreview, openPublish }: { selected: IcpVersion; total: number; validation: IcpValidation | null; preview: IcpPreview | null; editable: boolean; busy: boolean; runValidation: () => Promise<void>; runPreview: () => Promise<void>; openPublish: () => void }) {
  return <div className="icp-review"><div className="review-summary"><div><small>Version</small><b>{selected.source === "system_default" ? "System default" : `v${selected.version_number}`}</b></div><div><small>Status</small><b>{pretty(selected.status)}</b></div><div><small>ICP allocation</small><b>{total} / 40</b></div><div><small>Fixed components</small><b>35 Intent · 25 Engagement</b></div></div>{editable && <div className="review-actions"><button className="secondary-button" disabled={busy} onClick={() => void runValidation()}>Validate draft</button><button className="secondary-button" disabled={busy || total !== 40} onClick={() => void runPreview()}>Test against existing leads</button><button className="primary-button" disabled={busy || total !== 40 || validation?.valid !== true} onClick={openPublish}>Publish ICP</button></div>}{validation && <div className={`validation-result ${validation.valid ? "valid" : "invalid"}`}><b>{validation.valid ? "Validation passed" : "Draft is not publishable"}</b>{validation.problems.length > 0 && <ul>{validation.problems.map((problem) => <li key={problem}>{problem}</li>)}</ul>}</div>}{preview && <div className="preview-result"><header><div><span className="eyebrow">Non-destructive preview</span><h3>{preview.leads_evaluated} leads evaluated · {preview.changed_count} changed</h3></div><div>{Object.entries(preview.transitions).map(([transition, count]) => <span key={transition}>{count} {transition}</span>)}</div></header><div className="preview-leads">{preview.representative_changes.map((lead) => <article key={lead.lead_id}><div><b>{lead.lead_name}</b><span>{lead.company_name}</span></div><span>Current: {pretty(lead.current_classification)} {lead.current_score}</span><span>Proposed: {pretty(lead.proposed_classification)} {lead.proposed_score}</span><strong className={lead.score_delta >= 0 ? "positive" : "negative"}>{lead.score_delta >= 0 ? "+" : ""}{lead.score_delta}</strong><p>{lead.important_changed_matches.join(" ") || "Classification boundary changed under the proposed ICP."}</p></article>)}</div></div>}</div>;
}
