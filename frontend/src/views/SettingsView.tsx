import { useEffect, useState, type KeyboardEvent, type ReactNode } from "react";
import {
  generateEmailTemplate,
  getGoogleCalendarBookingLink,
  getLeadConfirmationAutomation,
  getEmailTemplates,
  getPublicIntakeConfig,
  getRoutingConfig,
  rotatePublicIntakeConfig,
  setLeadConfirmationAutomation,
  setGoogleCalendarBookingLink,
  saveEmailTemplate,
  updateRoutingConfig,
} from "../api";
import {
  opsGet,
  opsSave,
  type Capacity,
  type PolicyResponse,
} from "../operationsApi";
import { AccountProfilePanel } from "../components/AccountProfilePanel";
import { WorkspaceBusinessProfilePanel } from "../components/WorkspaceBusinessProfilePanel";
import type {
  AnalyticsMetadata,
  RoutingConfig,
  WorkspaceEmailTemplate,
} from "../types";
import { Panel, pretty } from "./productShared";
import { confirmAction } from "../v3/ui/confirm";
import { PageHeader } from "../v3/ui/primitives";

export function SettingsView({
  metadata,
  selectedSection = "",
  onIntegrations,
  onTeam,
  onIcp,
  onPolicies,
  onCompliance,
  onOpenSection,
  canViewTeam,
  canManageWorkspace,
  canViewIcp,
  canViewPolicies,
  canViewCompliance,
  canViewRouting,
  canManageRouting,
  canViewPublicIntake,
  canRotatePublicIntake,
  canManageEmail,
}: {
  metadata?: AnalyticsMetadata;
  selectedSection?: string;
  onIntegrations: () => void;
  onTeam: () => void;
  onIcp: () => void;
  onPolicies: () => void;
  onCompliance: () => void;
  onOpenSection: (section: string) => void;
  canViewTeam: boolean;
  canManageWorkspace: boolean;
  canViewIcp: boolean;
  canViewPolicies: boolean;
  canViewCompliance: boolean;
  canViewRouting: boolean;
  canManageRouting: boolean;
  canViewPublicIntake: boolean;
  canRotatePublicIntake: boolean;
  canManageEmail: boolean;
}) {
  const isCategory = selectedSection === "company" || selectedSection === "user";
  const overview = !selectedSection || selectedSection === "overview" || isCategory;
  const activeCategory =
    selectedSection === "user" || selectedSection === "users"
      ? "user"
      : "company";
  const isLegacyDetail = Boolean(selectedSection && !isCategory && selectedSection !== "overview");
  const show = (section: string) =>
    selectedSection === section;
  const tabKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const tabs = Array.from(
      event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]'),
    );
    const current = tabs.indexOf(event.target as HTMLButtonElement);
    const next =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? tabs.length - 1
          : (current + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) %
            tabs.length;
    tabs[next]?.focus();
    tabs[next]?.click();
  };
  return (
    <div
      className={`page-view operational-page${selectedSection ? " focused-page" : ""}`}
      data-settings-page={selectedSection || "overview"}
    >
      <PageHeader
        title={isLegacyDetail ? pretty(selectedSection.replaceAll("-", "_")) : "Settings"}
        subtitle={isLegacyDetail
          ? "Changes here apply to your current workspace."
          : "Your company workspace and your own Corral account."}
      />
      <div
        className="settings-category-tabs"
        role="tablist"
        aria-label="Settings categories"
        onKeyDown={tabKeyDown}
      >
        <button
          type="button"
          role="tab"
          id="settings-company-tab"
          aria-selected={activeCategory === "company"}
          aria-controls="settings-category-panel"
          tabIndex={activeCategory === "company" ? 0 : -1}
          className={activeCategory === "company" ? "active" : ""}
          onClick={() => onOpenSection("company")}
        >
          <span className="settings-tab-mark" aria-hidden="true">01</span>
          <span>Company</span>
          <small>Workspace configuration</small>
        </button>
        <button
          type="button"
          role="tab"
          id="settings-user-tab"
          aria-selected={activeCategory === "user"}
          aria-controls="settings-category-panel"
          tabIndex={activeCategory === "user" ? 0 : -1}
          className={activeCategory === "user" ? "active" : ""}
          onClick={() => onOpenSection("user")}
        >
          <span className="settings-tab-mark" aria-hidden="true">02</span>
          <span>User</span>
          <small>Account and team access</small>
        </button>
      </div>
      <section
        className="settings-grid"
        data-tour="settings"
        id="settings-category-panel"
        role="tabpanel"
        aria-labelledby={`settings-${activeCategory}-tab`}
        tabIndex={-1}
      >
        {overview && activeCategory === "company" && (
          <>
            <div className="settings-category-intro">
              <span className="eyebrow">Company</span>
              <p>Workspace identity, lead qualification, intake, routing, and connected services.</p>
            </div>
            <div className="settings-shortcut-grid">
              <SettingsLinkGroup number="01" title="Identity & qualification" description="Company profile and the criteria used to qualify leads.">
                <SettingsLink label="Workspace identity" onClick={() => onOpenSection("workspace")} />
                <SettingsLink label="Ideal Customer Profile" onClick={onIcp} disabled={!canViewIcp} />
              </SettingsLinkGroup>
              <SettingsLinkGroup number="02" title="Lead operations" description="Control how new leads enter the workspace and get assigned.">
                <SettingsLink label="Routing & review" onClick={() => onOpenSection("routing-distribution")} disabled={!canViewRouting} />
                <SettingsLink label="Public intake" onClick={() => onOpenSection("website-lead-form")} disabled={!canViewPublicIntake} />
              </SettingsLinkGroup>
              <SettingsLinkGroup number="03" title="Connections & messaging" description="Manage workspace-owned providers and reusable email copy.">
                <SettingsLink label="Integrations" onClick={onIntegrations} />
                <SettingsLink label="Email templates" onClick={() => onOpenSection("email-templates")} disabled={!canManageEmail} />
              </SettingsLinkGroup>
              <SettingsLinkGroup number="04" title="Governance" description="Workspace policy, privacy, and compliance controls.">
                <SettingsLink label="Segmenting & routing" onClick={onPolicies} disabled={!canViewPolicies} />
                <SettingsLink label="Privacy & compliance" onClick={onCompliance} disabled={!canViewCompliance} />
              </SettingsLinkGroup>
            </div>
          </>
        )}
        {overview && activeCategory === "user" && (
          <>
            <div className="settings-category-intro">
              <span className="eyebrow">User</span>
              <p>Your Corral account profile and the people who can access this workspace.</p>
            </div>
            <AccountProfilePanel />
            <Panel collapsible title="Users & permissions" eyebrow="Workspace access">
              <p className="panel-copy">
                Invite users, review roles, and make per-person permission changes.
                Backend authorization remains the source of truth for every action.
              </p>
              <button className="primary-button" disabled={!canViewTeam} onClick={onTeam}>
                {canViewTeam ? "Manage users" : "Members view permission required"}
              </button>
            </Panel>
          </>
        )}
        {show("workspace") && (
          <>
            <Panel collapsible title="Workspace" eyebrow="Tenant boundary">
              <div className="settings-facts">
                <Fact label="Name" value={metadata?.workspace_name || "Unavailable"} />
                <Fact label="Workspace ID" value={metadata?.workspace_id || "Unavailable"} />
                <Fact label="Data mode" value={pretty(metadata?.data_mode || "unknown")} />
                <Fact label="Analytics freshness" value={metadata ? new Date(metadata.generated_at).toLocaleString() : "Unavailable"} />
              </div>
            </Panel>
            <Panel collapsible title="Business identity for AI" eyebrow="Optional analysis context">
              <WorkspaceBusinessProfilePanel canView={canViewRouting} canManage={canManageWorkspace} />
            </Panel>
          </>
        )}
        {show("routing-distribution") && <RoutingWeightsPanel canView={canViewRouting} canManage={canManageRouting} />}
        {show("website-lead-form") && <PublicIntakePanel canView={canViewPublicIntake} canRotate={canRotatePublicIntake} />}
        {show("email-templates") && (
          <div className="settings-email-templates">
            <EmailTemplatesPanel canManage={canManageEmail} onIntegrations={onIntegrations} />
          </div>
        )}
        {show("users") && (
          <Panel collapsible title="Users & permissions" eyebrow="Workspace access">
            <p className="panel-copy">Invite users, review roles, and make per-person permission changes.</p>
            <button className="primary-button" disabled={!canViewTeam} onClick={onTeam}>Open users</button>
          </Panel>
        )}
        {show("icp") && (
          <Panel collapsible title="Ideal Customer Profile" eyebrow="Qualification policy">
            <p className="panel-copy">Describe who you sell to and test it against existing leads before publishing. Past scores keep the version they were scored with.</p>
            <button className="primary-button" disabled={!canViewIcp} onClick={onIcp}>Open ICP builder</button>
          </Panel>
        )}
        {show("policies") && (
          <Panel collapsible title="Routing & capacity" eyebrow="Workspace policy">
            <p className="panel-copy">Territories, response targets, rep limits and what the agent may do without asking.</p>
            <button className="primary-button" disabled={!canViewPolicies} onClick={onPolicies}>Open routing & capacity</button>
          </Panel>
        )}
        {show("compliance") && (
          <Panel collapsible title="Consent & governance" eyebrow="Workspace safeguards">
            <p className="panel-copy">Contact permissions, opt-outs, audit exports and data retention.</p>
            <button className="primary-button" disabled={!canViewCompliance} onClick={onCompliance}>Open consent & governance</button>
          </Panel>
        )}
      </section>
    </div>
  );
}

function SettingsLinkGroup({
  number,
  title,
  description,
  children,
}: {
  number: string;
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <details className="settings-link-group">
      <summary className="settings-link-group-heading">
        <span>{number}</span>
        <div><h2>{title}</h2><p>{description}</p></div>
        <span className="settings-collapsible-icon" aria-hidden="true" />
      </summary>
      <div className="settings-link-list">{children}</div>
    </details>
  );
}

function SettingsLink({
  label,
  onClick,
  disabled = false,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      className="settings-link"
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={disabled ? `${label}, permission required` : label}
    >
      <span>{label}</span>
      <span aria-hidden="true">→</span>
    </button>
  );
}

function EmailTemplatesPanel({
  canManage,
  onIntegrations,
}: {
  canManage: boolean;
  onIntegrations: () => void;
}) {
  const [templates, setTemplates] = useState<WorkspaceEmailTemplate[]>([]);
  const [automaticConfirmationEnabled, setAutomaticConfirmationEnabled] =
    useState(false);
  const [googleBookingUrl, setGoogleBookingUrl] = useState("");
  const [bookingUrlLoading, setBookingUrlLoading] = useState(true);
  const [bookingUrlBusy, setBookingUrlBusy] = useState(false);
  const [bookingUrlNotice, setBookingUrlNotice] = useState<string | null>(null);
  const [automationLoading, setAutomationLoading] = useState(true);
  const [automationBusy, setAutomationBusy] = useState(false);
  const [importantContext, setImportantContext] = useState<
    Record<WorkspaceEmailTemplate["kind"], string>
  >({ confirmation: "", follow_up: "" });
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  useEffect(() => {
    if (!canManage) return;
    void getEmailTemplates()
      .then(setTemplates)
      .catch((error) =>
        setNotice(
          error instanceof Error
            ? error.message
            : "Unable to load email templates",
        ),
      );
    void getLeadConfirmationAutomation()
      .then((setting) => setAutomaticConfirmationEnabled(setting.enabled))
      .catch((error) =>
        setNotice(
          error instanceof Error
            ? error.message
            : "Unable to load automatic confirmation setting",
        ),
      )
      .finally(() => setAutomationLoading(false));
    void getGoogleCalendarBookingLink()
      .then((setting) => setGoogleBookingUrl(setting.url))
      .catch((error) =>
        setBookingUrlNotice(
          error instanceof Error
            ? error.message
            : "Unable to load the Google Calendar booking link",
        ),
      )
      .finally(() => setBookingUrlLoading(false));
  }, [canManage]);
  const saveGoogleBookingUrl = async () => {
    setBookingUrlBusy(true);
    setBookingUrlNotice(null);
    try {
      const saved = await setGoogleCalendarBookingLink(googleBookingUrl.trim());
      setGoogleBookingUrl(saved.url);
      setBookingUrlNotice(
        saved.url
          ? "Google Calendar appointment link saved."
          : "Google Calendar appointment link removed.",
      );
    } catch (error) {
      setBookingUrlNotice(
        error instanceof Error
          ? error.message
          : "Unable to save the Google Calendar booking link",
      );
    } finally {
      setBookingUrlBusy(false);
    }
  };
  const toggleAutomaticConfirmation = async () => {
    setAutomationBusy(true);
    setNotice(null);
    try {
      const setting = await setLeadConfirmationAutomation(
        !automaticConfirmationEnabled,
      );
      setAutomaticConfirmationEnabled(setting.enabled);
      setNotice(
        setting.enabled
          ? "Automatic confirmations are on by default. Platform overrides remain unchanged."
          : "Automatic confirmations are off by default. Platform overrides remain unchanged.",
      );
    } catch (error) {
      setNotice(
        error instanceof Error
          ? error.message
          : "Unable to update automatic confirmation setting",
      );
    } finally {
      setAutomationBusy(false);
    }
  };
  const update = (
    kind: WorkspaceEmailTemplate["kind"],
    field: "subject" | "body" | "generation_method",
    value: string,
  ) =>
    setTemplates((current) =>
      current.map((item) =>
        item.kind === kind ? { ...item, [field]: value } : item,
      ),
    );
  const save = async (template: WorkspaceEmailTemplate) => {
    setBusy(`${template.kind}-save`);
    setNotice(null);
    try {
      const saved = await saveEmailTemplate(template.kind, {
        subject: template.subject,
        body: template.body,
        generation_method: template.generation_method,
      });
      setTemplates((current) =>
        current.map((item) => (item.kind === saved.kind ? saved : item)),
      );
      setNotice(
        `${template.kind === "confirmation" ? "Confirmation" : "Follow-up"} email sample saved.`,
      );
    } catch (error) {
      setNotice(
        error instanceof Error ? error.message : "Unable to save email sample",
      );
    } finally {
      setBusy(null);
    }
  };
  const generate = async (kind: WorkspaceEmailTemplate["kind"]) => {
    const sample = templates.find((item) => item.kind === kind);
    if (!sample) return;
    const generateFromScratch = sample.generation_method === "ai_full";
    setBusy(`${kind}-ai`);
    setNotice(null);
    try {
      const generated = await generateEmailTemplate(
        kind,
        generateFromScratch
          ? {
              generation_mode: "fresh",
              important_context: importantContext[kind].trim() || undefined,
            }
          : {
              subject: sample.subject,
              body: sample.body,
              generation_mode: "style",
            },
      );
      setTemplates((current) =>
        current.map((item) => (item.kind === kind ? generated : item)),
      );
      setNotice(
        generateFromScratch
          ? "A fresh AI email was generated from the essential context. Review it, then save to use it."
          : "Fresh AI copy generated in your sample’s style. Review it, then save to use it.",
      );
    } catch (error) {
      setNotice(
        error instanceof Error
          ? error.message
          : "Unable to generate email sample",
      );
    } finally {
      setBusy(null);
    }
  };
  if (!canManage)
    return (
      <Panel collapsible title="Email templates" eyebrow="Workspace messaging">
        <p className="panel-copy">
          Integration management permission is required to configure email
          templates.
        </p>
      </Panel>
    );
  const preview = (value: string) =>
    value
      .replaceAll("{{lead_name}}", "Alex")
      .replaceAll("{{company_name}}", "Northstar Labs")
      .replaceAll("{{product_or_service}}", "lead workflow automation")
      .replaceAll("{{job_title}}", "Sales Director");
  return (
    <Panel collapsible title="Email templates" eyebrow="Workspace messaging">
      <p className="panel-copy">
        Edit sample emails here, or let AI create a fresh reusable email from
        the essential context alone. Generated copy is a draft until you save
        it. Platform-specific confirmation switches are on each integration
        card. Manual follow-up emails are unaffected.
      </p>
      <div className="email-automation-control">
        <div className="email-automation-copy">
          <div className="email-automation-title">
            <h3>Workspace default for confirmations</h3>
            <span
              className={`email-automation-state${automaticConfirmationEnabled ? " is-on" : ""}`}
            >
              {automationLoading
                ? "Loading"
                : automaticConfirmationEnabled
                  ? "On by default"
                  : "Off by default"}
            </span>
          </div>
          <p>
            {automaticConfirmationEnabled
              ? "Sources without their own setting send the saved confirmation email. A platform override below takes priority."
              : "Sources without their own setting do not send confirmations. You can turn selected platforms on below."}
          </p>
        </div>
        <button
          type="button"
          className={`email-automation-toggle${automaticConfirmationEnabled ? " is-on" : ""}`}
          role="switch"
          aria-checked={automaticConfirmationEnabled}
          aria-label="Default automatic new-lead confirmation emails"
          disabled={automationLoading || automationBusy}
          onClick={() => void toggleAutomaticConfirmation()}
        >
          <span className="email-automation-track" aria-hidden="true">
            <i />
          </span>
          <span>
            {automationLoading
              ? "Loading…"
              : automationBusy
                ? "Saving…"
              : automaticConfirmationEnabled
                ? "Default on"
                : "Default off"}
          </span>
        </button>
      </div>
      <div className="email-automation-platform-link">
        <p className="panel-copy">
          The workspace setting is the default. Each platform can override it
          from its integration card.
        </p>
        <button className="button-quiet" type="button" onClick={onIntegrations}>
          Configure platform settings →
        </button>
      </div>
      <div className="email-automation-control google-booking-link-control">
        <div className="email-automation-copy">
          <div className="email-automation-title">
            <h3>Google Calendar appointment link</h3>
            <span className={`email-automation-state${googleBookingUrl ? " is-on" : ""}`}>
              {bookingUrlLoading ? "Loading" : googleBookingUrl ? "Configured" : "Not set"}
            </span>
          </div>
          <p>
            Add the public appointment-schedule link from Google Calendar. It is
            used in meeting drafts only when Calendly is not connected.
          </p>
          <label className="follow-up-field">
            Google Calendar booking URL
            <input
              type="url"
              inputMode="url"
              value={googleBookingUrl}
              placeholder="https://calendar.app.google/…"
              disabled={bookingUrlLoading || bookingUrlBusy}
              onChange={(event) => setGoogleBookingUrl(event.target.value)}
            />
          </label>
          {bookingUrlNotice && (
            <p role="status" className="panel-copy">{bookingUrlNotice}</p>
          )}
        </div>
        <button
          type="button"
          className="button-quiet"
          disabled={bookingUrlLoading || bookingUrlBusy}
          onClick={() => void saveGoogleBookingUrl()}
        >
          {bookingUrlBusy ? "Saving…" : "Save booking link"}
        </button>
      </div>
      <div className="email-template-grid">
        {templates.map((template) => {
          const confirmation = template.kind === "confirmation";
          return (
            <article className="email-template-card" key={template.kind}>
              <div className="email-template-heading">
                <h3>
                  {confirmation ? "New-lead confirmation" : "Lead follow-up"}
                </h3>
                <span>
                  {confirmation
                    ? "Automatic · platform settings"
                    : "Human-send only"}
                </span>
              </div>
              <p className="panel-copy">
                {confirmation
                  ? "Acknowledges receipt using this sample."
                  : "Used as the workspace style for lead-specific drafts."}{" "}
                Available fields: <code>{"{{lead_name}}"}</code>,{" "}
                <code>{"{{company_name}}"}</code>,{" "}
                <code>{"{{product_or_service}}"}</code>,{" "}
                <code>{"{{job_title}}"}</code>.
              </p>
              <label className="email-template-field">
                Sample subject
                <input
                  value={template.subject}
                  onChange={(event) =>
                    update(template.kind, "subject", event.target.value)
                  }
                  disabled={busy !== null}
                />
              </label>
              <label className="email-template-field">
                Sample body
                <textarea
                  rows={8}
                  value={template.body}
                  onChange={(event) =>
                    update(template.kind, "body", event.target.value)
                  }
                  disabled={busy !== null}
                />
              </label>
              <div className="email-template-actions">
                <label>
                  Draft style
                  <select
                    value={template.generation_method}
                    onChange={(event) =>
                      update(
                        template.kind,
                        "generation_method",
                        event.target.value,
                      )
                    }
                    disabled={busy !== null}
                  >
                    <option value="manual">Use my saved sample</option>
                    <option value="ai">AI draft in my saved style</option>
                    <option value="ai_full">Generate fully with AI (no format needed)</option>
                  </select>
                </label>
                {template.generation_method === "ai_full" && (
                  <label className="email-template-field email-template-context">
                    Important info (optional)
                    <textarea
                      rows={3}
                      value={importantContext[template.kind]}
                      onChange={(event) =>
                        setImportantContext((current) => ({
                          ...current,
                          [template.kind]: event.target.value,
                        }))
                      }
                      disabled={busy !== null}
                      placeholder="Add only the key goal, offer, audience, or details AI should know. No format needed."
                    />
                  </label>
                )}
                <button
                  className="button-quiet"
                  disabled={busy !== null}
                  onClick={() => void generate(template.kind)}
                >
                  {busy === `${template.kind}-ai`
                    ? "Generating…"
                    : template.generation_method === "ai_full"
                      ? "Generate with AI"
                      : "Generate sample with AI"}
                </button>
                <button
                  className="primary-button"
                  disabled={
                    busy !== null ||
                    !template.subject.trim() ||
                    !template.body.trim()
                  }
                  onClick={() => void save(template)}
                >
                  {busy === `${template.kind}-save` ? "Saving…" : "Save sample"}
                </button>
              </div>
              <div className="email-template-preview">
                <small>PREVIEW</small>
                <b>{preview(template.subject)}</b>
                <p>{preview(template.body)}</p>
              </div>
            </article>
          );
        })}
      </div>
      {notice && (
        <div className="notice" role="status">
          {notice}
        </div>
      )}
    </Panel>
  );
}

function routingPercentages(members: RoutingConfig["members"]) {
  if (!members.length) return {};
  const weights = members.map((member) =>
    Number.isFinite(member.weight) && member.weight > 0 ? member.weight : 1,
  );
  const totalWeight = weights.reduce((sum, weight) => sum + weight, 0);
  let assigned = 0;
  return Object.fromEntries(
    members.map((member, index) => {
      const share =
        index === members.length - 1
          ? Math.round((100 - assigned) * 100) / 100
          : Math.round((weights[index] / totalWeight) * 10000) / 100;
      assigned += share;
      return [member.user_id, share];
    }),
  );
}

function RoutingWeightsPanel({
  canView,
  canManage,
}: {
  canView: boolean;
  canManage: boolean;
}) {
  const [config, setConfig] = useState<RoutingConfig | null>(null);
  const [percentages, setPercentages] = useState<Record<string, number>>({});
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [manualReview, setManualReview] = useState<RoutingConfig["manual_review"]>({
    personal_email: true,
    missing_person_name: true,
    missing_company_name: true,
    low_confidence: false,
    contradictory_evidence: false,
    missing_qualification_information: false,
    provider_failure: false,
  });
  const [manualReviewBusy, setManualReviewBusy] = useState(false);
  const [manualReviewNotice, setManualReviewNotice] = useState<string | null>(null);
  const [policy, setPolicy] = useState<PolicyResponse | null>(null);
  const [capacityBusy, setCapacityBusy] = useState(false);
  const [capacityNotice, setCapacityNotice] = useState<string | null>(null);
  useEffect(() => {
    if (!canView) return;
    void getRoutingConfig()
      .then((value) => {
        setConfig(value);
        setPercentages(routingPercentages(value.members));
        setManualReview(value.manual_review);
      })
      .catch((error) =>
        setNotice(
          error instanceof Error
            ? error.message
            : "Unable to load routing distribution",
        ),
      );
    void opsGet<PolicyResponse>("/v1/ops/policy")
      .then(setPolicy)
      .catch((error) =>
        setCapacityNotice(
          error instanceof Error ? error.message : "Unable to load capacity settings",
        ),
      );
  }, [canView]);
  const totalPercentage =
    config?.members.reduce(
      (total, member) => total + (percentages[member.user_id] ?? 0),
      0,
    ) ?? 0;
  const percentagesValid =
    Boolean(config?.members.length) &&
    config!.members.every((member) => {
      const percentage = percentages[member.user_id];
      return (
        Number.isFinite(percentage) && percentage >= 0.01 && percentage <= 100
      );
    }) &&
    Math.abs(totalPercentage - 100) < 0.005;
  const save = async () => {
    setBusy(true);
    setNotice(null);
    try {
      const value = await updateRoutingConfig(percentages);
      setConfig(value);
      setPercentages(routingPercentages(value.members));
      setNotice(
        "Routing percentages saved. New leads will use the updated shares.",
      );
    } catch (error) {
      setNotice(
        error instanceof Error
          ? error.message
          : "Unable to save routing ratios",
      );
    } finally {
      setBusy(false);
    }
  };
  const saveManualReview = async () => {
    setManualReviewBusy(true);
    setManualReviewNotice(null);
    try {
      const value = await updateRoutingConfig(undefined, manualReview);
      setConfig(value);
      setManualReview(value.manual_review);
      setManualReviewNotice(
        "Saved. New and requalified leads will use these rules; existing review decisions are unchanged.",
      );
    } catch (error) {
      setManualReviewNotice(
        error instanceof Error ? error.message : "Unable to save manual-review rules",
      );
    } finally {
      setManualReviewBusy(false);
    }
  };
  const updateCapacity = (userId: string, patch: Partial<Capacity>) => {
    setPolicy((current) => {
      if (!current) return current;
      const existing = current.configuration.capacity.find((item) => item.user_id === userId) || {
        user_id: userId,
        max_open_leads: 30,
        available: true,
        available_until: null,
      };
      return {
        ...current,
        configuration: {
          ...current.configuration,
          capacity: [
            ...current.configuration.capacity.filter((item) => item.user_id !== userId),
            { ...existing, ...patch },
          ],
        },
      };
    });
    setCapacityNotice(null);
  };
  const saveCapacity = async () => {
    if (!policy) return;
    setCapacityBusy(true);
    setCapacityNotice(null);
    try {
      const saved = await opsSave<{ configuration: PolicyResponse["configuration"]; version: number }>(
        "/v1/ops/policy",
        { configuration: policy.configuration, version: policy.version },
        "PUT",
      );
      setPolicy((current) => current ? { ...current, ...saved } : current);
      setCapacityNotice("Capacity settings saved.");
    } catch (error) {
      setCapacityNotice(error instanceof Error ? error.message : "Unable to save capacity settings");
    } finally {
      setCapacityBusy(false);
    }
  };
  if (!canView)
    return (
      <Panel collapsible title="Routing distribution" eyebrow="Workspace assignment">
        <p className="panel-copy">Routing visibility permission required.</p>
      </Panel>
    );
  return (
    <Panel collapsible title="Routing distribution" eyebrow="Workspace assignment">
      <p className="panel-copy">
        Set each eligible member’s share as a percentage; all shares must total
        100%. New leads are routed by relative workload, and existing
        assignments are unchanged.
      </p>
      {config?.members.length ? (
        <div className="routing-ratio-list">
          {config.members.map((member) => (
            <div className="routing-ratio-row" key={member.user_id}>
              <div>
                <b>{member.display_name}</b>
                <small>
                  {member.role.replaceAll("_", " ")} · {member.open_leads} open
                  lead{member.open_leads === 1 ? "" : "s"}
                </small>
              </div>
              <label>
                <span>Share</span>
                <div className="routing-percentage-control">
                  <input
                    aria-label={`Routing percentage for ${member.display_name}`}
                    type="number"
                    min="0.01"
                    max="100"
                    step="0.01"
                    disabled={!canManage || busy}
                    value={percentages[member.user_id] ?? 0}
                    onChange={(event) => {
                      setNotice(null);
                      setPercentages((current) => ({
                        ...current,
                        [member.user_id]: Number(event.target.value),
                      }));
                    }}
                  />
                  <b aria-hidden="true">%</b>
                </div>
              </label>
            </div>
          ))}
        </div>
      ) : (
        <p className="panel-copy">
          No active workspace member with lead assignment permission is
          available.
        </p>
      )}
      {config?.members.length ? (
        <div
          className={`routing-percent-total ${percentagesValid ? "valid" : "invalid"}`}
          role="status"
        >
          <span>Total share</span>
          <b>{totalPercentage.toFixed(2)}%</b>
          <small>
            {percentagesValid ? "Ready to save" : "Shares must total 100%"}
          </small>
        </div>
      ) : null}
      {canManage && config?.members.length ? (
        <button
          className="primary-button"
          disabled={busy || !percentagesValid}
          onClick={() => void save()}
        >
          {busy ? "Saving…" : "Save percentages"}
        </button>
      ) : null}
      {notice && (
        <div className="notice" role="status">
          {notice}
        </div>
      )}
      <div className="routing-manual-review-block">
        <div className="section-heading">
          <div>
            <span className="eyebrow">Lead review rules</span>
            <h3>When should a lead go to Manual Review?</h3>
          </div>
        </div>
        <p className="panel-copy">
          By default, personal email addresses and missing person or company names are reviewed.
          Other checks are optional. Platform confirmation settings do not
          change these review rules.
        </p>
        <div className="manual-review-settings-list">
          {([
            ["personal_email", "Personal email address", "Review leads using common personal mailbox providers."],
            ["missing_person_name", "Person name is missing", "Review leads without a usable contact name."],
            ["missing_company_name", "Company name is missing", "Review leads without a usable company name."],
            ["low_confidence", "Low analysis confidence", "Also review analysis below the configured confidence threshold."],
            ["contradictory_evidence", "Contradictory evidence", "Also review analysis that contains conflicting evidence."],
            ["missing_qualification_information", "Other qualification details are missing", "Also review missing items such as industry, budget, or timeline."],
            ["provider_failure", "Analysis provider failed", "Also review leads when provider analysis fails and fallback results are used."],
          ] as const).map(([key, label, description]) => (
            <label className="manual-review-setting" key={key}>
              <input
                type="checkbox"
                checked={manualReview[key]}
                disabled={!canManage || manualReviewBusy}
                onChange={(event) => {
                  setManualReviewNotice(null);
                  setManualReview((current) => ({
                    ...current,
                    [key]: event.target.checked,
                  }));
                }}
              />
              <span>
                <b>{label}</b>
                <small>{description}</small>
              </span>
            </label>
          ))}
        </div>
        {canManage && (
          <button
            className="primary-button"
            disabled={manualReviewBusy}
            onClick={() => void saveManualReview()}
          >
            {manualReviewBusy ? "Saving…" : "Save review rules"}
          </button>
        )}
        {manualReviewNotice && (
          <div className="notice" role="status">{manualReviewNotice}</div>
        )}
      </div>
      {policy && (
        <div className="routing-capacity-block">
          <div className="section-heading">
            <div>
              <span className="eyebrow">Availability guardrails</span>
              <h3>Representative capacity</h3>
            </div>
          </div>
          <p className="panel-copy">Limit open assignments and pause a member from receiving new leads while keeping routing shares in one place.</p>
          <div className="ops-table-wrap">
            <table className="ops-table routing-capacity-table">
              <thead><tr><th>Member</th><th>Open leads</th><th>Maximum</th><th>Available</th><th>Check-in expiry</th></tr></thead>
              <tbody>{policy.members.map((member) => {
                const state = policy.configuration.capacity.find((value) => value.user_id === member.user_id);
                return <tr key={member.user_id}>
                  <td data-label="Member">{member.name}</td><td data-label="Open leads">{member.open_leads}</td>
                  <td data-label="Maximum"><input aria-label={`${member.name} maximum open leads`} type="number" min={0} max={10000} value={state?.max_open_leads ?? 30} disabled={!canManage || capacityBusy} onChange={(event) => updateCapacity(member.user_id, { max_open_leads: Number(event.target.value) })} /></td>
                  <td data-label="Available"><input aria-label={`${member.name} available`} type="checkbox" checked={state?.available ?? true} disabled={!canManage || capacityBusy} onChange={(event) => updateCapacity(member.user_id, { available: event.target.checked, available_until: null })} /></td>
                  <td data-label="Check-in expiry">{state?.available_until ? new Date(state.available_until).toLocaleString() : "Manual setting"}</td>
                </tr>;
              })}</tbody>
            </table>
          </div>
          {canManage && <button className="primary-button" disabled={capacityBusy} onClick={() => void saveCapacity()}>{capacityBusy ? "Saving…" : "Save capacity"}</button>}
          {capacityNotice && <div className="notice" role="status">{capacityNotice}</div>}
        </div>
      )}
    </Panel>
  );
}

function PublicIntakePanel({
  canView,
  canRotate,
}: {
  canView: boolean;
  canRotate: boolean;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const publicIntakeBaseUrl =
    import.meta.env.VITE_PUBLIC_INTAKE_BASE_URL?.replace(/\/$/, "");
  const displayUrl = (value: string) =>
    publicIntakeBaseUrl
      ? `${publicIntakeBaseUrl}${new URL(value).pathname}`
      : value;
  const embedCode = url
    ? `<form action="${url}" method="post">\n  <input name="full_name" required placeholder="Your name">\n  <input type="email" name="email" required placeholder="Work email">\n  <input name="company_name" required placeholder="Company">\n  <input name="product_or_service" required placeholder="What do you need?">\n  <textarea name="message" required placeholder="Tell us about your inquiry"></textarea>\n  <button type="submit">Send inquiry</button>\n</form>`
    : "";
  useEffect(() => {
    if (!canView) return;
    void getPublicIntakeConfig()
      .then((value) => setUrl(displayUrl(value.url)))
      .catch((error) =>
        setNotice(
          error instanceof Error
            ? error.message
            : "Unable to load website form URL",
        ),
      );
  }, [canView]);
  const copy = async () => {
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      setNotice("Public form URL copied.");
    } catch {
      setNotice("Copy was blocked; select the URL and copy it manually.");
    }
  };
  const rotate = async () => {
    if (!(await confirmAction({ title: "Rotate the form URL?", message: "The old website form URL stops accepting leads immediately. Update your website with the new one.", confirmLabel: "Rotate URL", tone: "danger" })))
      return;
    setBusy(true);
    setNotice(null);
    try {
      const value = await rotatePublicIntakeConfig();
      setUrl(displayUrl(value.url));
      setNotice(
        "New public form URL generated. Update your website form action.",
      );
    } catch (error) {
      setNotice(
        error instanceof Error
          ? error.message
          : "Unable to rotate public form URL",
      );
    } finally {
      setBusy(false);
    }
  };
  if (!canView)
    return (
      <Panel collapsible title="Website lead form" eyebrow="Public intake">
        <p className="panel-copy">Lead view permission required.</p>
      </Panel>
    );
  const copyEmbed = async () => {
    if (!embedCode) return;
    try {
      await navigator.clipboard.writeText(embedCode);
      setNotice("Website form HTML copied.");
    } catch {
      setNotice("Copy was blocked; select the form HTML manually.");
    }
  };
  return (
    <Panel collapsible title="Website lead form" eyebrow="Public intake">
      <p className="panel-copy">
        Send your website form to this URL. Each submission enters analysis,
        qualification, routing, Slack notification and CRM workflows
        automatically.
      </p>
      <div className="public-intake-url">
        <input
          aria-label="Public website lead form URL"
          readOnly
          value={url || "Generating secure URL…"}
        />
        <button
          className="button-quiet"
          disabled={!url}
          onClick={() => void copy()}
        >
          Copy URL
        </button>
      </div>
      <p className="panel-copy">
        Email is included and required so the connected Gmail account can send
        an automatic confirmation to the lead. Required fields:{" "}
        <code>full_name</code>, <code>email</code>, <code>company_name</code>,{" "}
        <code>product_or_service</code>, and <code>message</code>.
      </p>
      {url && (
        <>
          <textarea
            className="embed-code"
            aria-label="Website form HTML"
            readOnly
            value={embedCode}
          />
          <div className="public-intake-actions">
            <button className="button-quiet" onClick={() => void copyEmbed()}>
              Copy form HTML
            </button>
            {canRotate && (
              <button
                className="button-quiet"
                disabled={busy}
                onClick={() => void rotate()}
              >
                {busy ? "Rotating…" : "Rotate URL"}
              </button>
            )}
          </div>
        </>
      )}
      {notice && (
        <div className="notice" role="status">
          {notice}
        </div>
      )}
    </Panel>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="fact">
      <span>{label}</span>
      <b>{value}</b>
    </div>
  );
}
