import { useEffect, useState } from "react";
import {
  generateEmailTemplate,
  getEmailTemplates,
  getPublicIntakeConfig,
  getRoutingConfig,
  rotatePublicIntakeConfig,
  saveEmailTemplate,
  updateRoutingConfig,
} from "../api";
import { AccountProfilePanel } from "../components/AccountProfilePanel";
import type {
  AnalyticsMetadata,
  RoutingConfig,
  WorkspaceEmailTemplate,
} from "../types";
import { Panel, pretty } from "./productShared";

export function SettingsView({
  metadata,
  selectedSection = "",
  onIntegrations,
  onTeam,
  onIcp,
  canViewTeam,
  canViewIcp,
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
  canViewTeam: boolean;
  canViewIcp: boolean;
  canViewRouting: boolean;
  canManageRouting: boolean;
  canViewPublicIntake: boolean;
  canRotatePublicIntake: boolean;
  canManageEmail: boolean;
}) {
  const show = (section: string) =>
    !selectedSection || selectedSection === section;
  return (
    <div
      className={`page-view operational-page${selectedSection ? " focused-page" : ""}`}
      data-settings-page={selectedSection || "overview"}
    >
      <header className="page-hero compact-hero">
        <div>
          <span className="eyebrow">Workspace settings</span>
          <h1>
            {selectedSection ? (
              pretty(selectedSection.replaceAll("-", "_"))
            ) : (
              "Settings"
            )}
          </h1>
          <p>
            {selectedSection
              ? "Manage this section for your current workspace."
              : "Workspace identity, data provenance and team access stay visible here."}
          </p>
        </div>
      </header>
      <section className="settings-grid" data-tour="settings">
        {!selectedSection && <AccountProfilePanel />}
        {show("workspace") && (
          <Panel title="Workspace" eyebrow="Tenant boundary">
            <div className="settings-facts">
              <Fact
                label="Name"
                value={metadata?.workspace_name || "Unavailable"}
              />
              <Fact
                label="Workspace ID"
                value={metadata?.workspace_id || "Unavailable"}
              />
              <Fact
                label="Data mode"
                value={pretty(metadata?.data_mode || "unknown")}
              />
              <Fact
                label="Analytics freshness"
                value={
                  metadata
                    ? new Date(metadata.generated_at).toLocaleString()
                    : "Unavailable"
                }
              />
            </div>
          </Panel>
        )}
        {!selectedSection && (
          <Panel title="External systems" eyebrow="Provider connections">
            <p className="panel-copy">
              Manage workspace-owned email, CRM and notification connections.
              Stored credentials are represented only by safe state metadata.
            </p>
            <button className="primary-button" onClick={onIntegrations}>
              Open Integration Center →
            </button>
          </Panel>
        )}
        <Panel title="Users & permissions" eyebrow="Workspace access">
          <p className="panel-copy">
            Invite users, review roles and make deliberate per-person permission
            changes. Backend authorization remains the source of truth for every
            action.
          </p>
          <button
            className="primary-button"
            disabled={!canViewTeam}
            onClick={onTeam}
          >
            {canViewTeam ? "Open Users →" : "Members view permission required"}
          </button>
        </Panel>
        {show("routing-distribution") && (
          <RoutingWeightsPanel
            canView={canViewRouting}
            canManage={canManageRouting}
          />
        )}
        {show("website-lead-form") && (
          <PublicIntakePanel
            canView={canViewPublicIntake}
            canRotate={canRotatePublicIntake}
          />
        )}
        {show("email-templates") && (
          <div className="settings-email-templates">
            <EmailTemplatesPanel canManage={canManageEmail} />
          </div>
        )}
        <Panel title="Ideal Customer Profile" eyebrow="Qualification policy">
          <p className="panel-copy">
            Build, validate and safely test the workspace-specific 40-point ICP.
            Published versions remain immutable and historical scores retain
            their exact policy reference.
          </p>
          <button
            className="primary-button"
            disabled={!canViewIcp}
            onClick={onIcp}
          >
            {canViewIcp ? "Open ICP Builder →" : "ICP view permission required"}
          </button>
        </Panel>
        <Panel title="Data range" eyebrow="Analytics contract">
          <p className="panel-copy">
            Weekly, monthly and annual windows are available. Period-over-period
            change is withheld when the prior period has no valid denominator.
          </p>
        </Panel>
        <Panel title="Product state" eyebrow="Release readiness">
          <p className="panel-copy">
            Analytics, team controls, workspace-specific ICP qualification,
            secure exports and certified provider workflows are active.
          </p>
        </Panel>
      </section>
    </div>
  );
}

function EmailTemplatesPanel({ canManage }: { canManage: boolean }) {
  const [templates, setTemplates] = useState<WorkspaceEmailTemplate[]>([]);
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
  }, [canManage]);
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
    setBusy(`${kind}-ai`);
    setNotice(null);
    try {
      const generated = await generateEmailTemplate(kind, {
        subject: sample.subject,
        body: sample.body,
      });
      setTemplates((current) =>
        current.map((item) => (item.kind === kind ? generated : item)),
      );
      setNotice(
        "Fresh AI copy generated in your sample’s style. Review it, then save to use it.",
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
      <Panel title="Email templates" eyebrow="Workspace messaging">
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
    <Panel title="Email templates" eyebrow="Workspace messaging">
      <p className="panel-copy">
        Edit sample emails here. AI uses the current sample for tone and voice,
        then writes fresh wording while preserving its merge fields. Generated
        copy is a draft until you save it. Confirmation is sent automatically
        when a new lead arrives. Follow-up is never sent automatically; a
        teammate reviews the lead-specific draft and clicks Send in Corral.
      </p>
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
                  {confirmation ? "Automatic on intake" : "Human-send only"}
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
                  </select>
                </label>
                <button
                  className="button-quiet"
                  disabled={busy !== null}
                  onClick={() => void generate(template.kind)}
                >
                  {busy === `${template.kind}-ai`
                    ? "Generating…"
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
  useEffect(() => {
    if (!canView) return;
    void getRoutingConfig()
      .then((value) => {
        setConfig(value);
        setPercentages(routingPercentages(value.members));
      })
      .catch((error) =>
        setNotice(
          error instanceof Error
            ? error.message
            : "Unable to load routing distribution",
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
  if (!canView)
    return (
      <Panel title="Routing distribution" eyebrow="Workspace assignment">
        <p className="panel-copy">Routing visibility permission required.</p>
      </Panel>
    );
  return (
    <Panel title="Routing distribution" eyebrow="Workspace assignment">
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
    if (
      !window.confirm(
        "Rotate this URL? The old website form URL will stop accepting leads.",
      )
    )
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
      <Panel title="Website lead form" eyebrow="Public intake">
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
    <Panel title="Website lead form" eyebrow="Public intake">
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
          <button className="button-quiet" onClick={() => void copyEmbed()}>
            Copy form HTML
          </button>
        </>
      )}
      {canRotate && (
        <button
          className="button-quiet"
          disabled={busy}
          onClick={() => void rotate()}
        >
          {busy ? "Rotating…" : "Rotate URL"}
        </button>
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
