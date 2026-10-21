import { useEffect, useState, type FormEvent } from "react";
import {
  opsGet,
  opsSave,
  type Policy,
  type PolicyResponse,
  type WorkspaceBusinessProfile,
} from "../operationsApi";
import "./workspaceBusinessProfile.css";

type BusinessProfileForm = {
  business_name: string;
  alternate_names: string;
  website: string;
  email_domains: string;
  description: string;
  products_services: string;
  lead_distinction_notes: string;
};

type WebsiteProfileAnalysis = {
  suggestions: WorkspaceBusinessProfile;
  source: string;
  evidence: { field: string; value: string; excerpt: string; source: string }[];
  limitations: string;
};

const emptyForm: BusinessProfileForm = {
  business_name: "",
  alternate_names: "",
  website: "",
  email_domains: "",
  description: "",
  products_services: "",
  lead_distinction_notes: "",
};

const lines = (value: string) =>
  value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);

const toForm = (profile: WorkspaceBusinessProfile): BusinessProfileForm => ({
  business_name: profile.business_name ?? "",
  alternate_names: (profile.alternate_names ?? []).join("\n"),
  website: profile.website ?? "",
  email_domains: (profile.email_domains ?? []).join("\n"),
  description: profile.description ?? "",
  products_services: profile.products_services ?? "",
  lead_distinction_notes: profile.lead_distinction_notes ?? "",
});

export function WorkspaceBusinessProfilePanel({
  canView,
  canManage,
}: {
  canView: boolean;
  canManage: boolean;
}) {
  const [policy, setPolicy] = useState<PolicyResponse | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [analyzingWebsite, setAnalyzingWebsite] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [websiteAnalysis, setWebsiteAnalysis] =
    useState<WebsiteProfileAnalysis | null>(null);
  const [websiteAnalysisNotice, setWebsiteAnalysisNotice] = useState<
    string | null
  >(null);
  const [websiteAnalysisError, setWebsiteAnalysisError] = useState<
    string | null
  >(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (!canView) {
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    void opsGet<PolicyResponse>("/v1/ops/policy", controller.signal)
      .then((response) => {
        if (controller.signal.aborted) return;
        setPolicy(response);
        setForm(toForm(response.configuration.business_profile));
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        setError(
          reason instanceof Error
            ? reason.message
            : "Unable to load business context",
        );
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [canView, reloadKey]);

  const update = (key: keyof BusinessProfileForm, value: string) => {
    setForm((current) => ({ ...current, [key]: value }));
    setNotice(null);
    if (key === "website") {
      setWebsiteAnalysis(null);
      setWebsiteAnalysisNotice(null);
      setWebsiteAnalysisError(null);
    }
  };

  async function analyzeWebsite() {
    if (!canManage || !form.website.trim()) return;
    setAnalyzingWebsite(true);
    setWebsiteAnalysis(null);
    setWebsiteAnalysisNotice(null);
    setWebsiteAnalysisError(null);
    try {
      const result = await opsSave<WebsiteProfileAnalysis>(
        "/v1/ops/business-profile/analyze",
        { website: form.website.trim() },
      );
      setForm((current) => ({
        ...current,
        business_name:
          current.business_name.trim() || result.suggestions.business_name,
        alternate_names:
          current.alternate_names.trim() ||
          result.suggestions.alternate_names.join("\n"),
        email_domains:
          current.email_domains.trim() ||
          result.suggestions.email_domains.join("\n"),
        description:
          current.description.trim() || result.suggestions.description,
        products_services:
          current.products_services.trim() ||
          result.suggestions.products_services,
      }));
      setWebsiteAnalysis(result);
      setWebsiteAnalysisNotice(
        "Website analyzed. Suggestions were added only to empty fields; review them before saving.",
      );
    } catch (reason) {
      setWebsiteAnalysisError(
        reason instanceof Error
          ? reason.message
          : "Unable to analyze this website.",
      );
    } finally {
      setAnalyzingWebsite(false);
    }
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!policy || !canManage) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    const businessProfile: WorkspaceBusinessProfile = {
      business_name: form.business_name.trim(),
      alternate_names: lines(form.alternate_names),
      website: form.website.trim(),
      email_domains: lines(form.email_domains).map((domain) =>
        domain.replace(/^@/, "").toLowerCase(),
      ),
      description: form.description.trim(),
      products_services: form.products_services.trim(),
      lead_distinction_notes: form.lead_distinction_notes.trim(),
    };
    try {
      const saved = await opsSave<{
        configuration: Policy;
        version: number;
      }>(
        "/v1/ops/policy",
        {
          configuration: {
            ...policy.configuration,
            business_profile: businessProfile,
          },
          version: policy.version,
        },
        "PUT",
      );
      setPolicy((current) =>
        current ? { ...current, ...saved } : current,
      );
      setForm(toForm(saved.configuration.business_profile));
      setNotice(
        "Business context saved. It will be used for new and re-run lead analyses; existing analyses are unchanged.",
      );
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Unable to save business context",
      );
    } finally {
      setBusy(false);
    }
  }

  if (!canView) {
    return (
      <p className="panel-copy">
        Workspace settings visibility permission is required to view this
        profile.
      </p>
    );
  }

  if (loading) {
    return <p className="panel-copy" role="status">Loading business context…</p>;
  }

  return (
    <div className="workspace-business-profile">
      <p className="panel-copy">
        Add your organization’s names and what it does so AI can distinguish
        your CRM-owning business from a prospective lead’s company. It is
        context, not proof that an email sender is a lead. The selected reasoning
        provider uses it for email lead detection and new or re-run analyses.
        Routine marketing emails sent to this workspace are treated as unrelated,
        while genuine inquiries and replies in existing sales conversations
        remain eligible. Existing lead analyses are unchanged.
      </p>
      {error && (
        <div className="notice" role="alert">
          <span>{error}</span>
          {error.includes("changed elsewhere") && (
            <button
              className="button-quiet"
              type="button"
              onClick={() => setReloadKey((value) => value + 1)}
            >
              Reload settings
            </button>
          )}
        </div>
      )}
      {!canManage && (
        <p className="workspace-business-profile-readonly">
          Only workspace administrators can edit this optional profile.
        </p>
      )}
      <form className="workspace-business-profile-form" onSubmit={save}>
        <label>
          <span>Business name</span>
          <input
            maxLength={255}
            value={form.business_name}
            disabled={!canManage || busy || analyzingWebsite}
            onChange={(event) => update("business_name", event.target.value)}
            placeholder="Your organization’s name"
          />
        </label>
        <label>
          <span>Other names or brands <small>One per line</small></span>
          <textarea
            maxLength={2000}
            rows={2}
            value={form.alternate_names}
            disabled={!canManage || busy || analyzingWebsite}
            onChange={(event) => update("alternate_names", event.target.value)}
            placeholder={"Trading names, brands, or former names"}
          />
        </label>
        <div className="workspace-business-profile-row">
          <div className="workspace-business-profile-website-column">
            <label>
              <span>Website</span>
              <input
                type="url"
                maxLength={500}
                value={form.website}
                disabled={!canManage || busy || analyzingWebsite}
                onChange={(event) => update("website", event.target.value)}
                placeholder="https://example.com"
              />
            </label>
            <button
              className="secondary-button workspace-business-profile-analyze"
              type="button"
              onClick={() => void analyzeWebsite()}
              disabled={
                !canManage || busy || analyzingWebsite || !form.website.trim()
              }
            >
              {analyzingWebsite ? "Analyzing…" : "Analyze website"}
            </button>
            <small className="workspace-business-profile-hint">
              Reads this page with your workspace AI provider. You review suggestions before saving.
            </small>
            {websiteAnalysisError && (
              <small className="workspace-business-profile-analysis-error" role="alert">
                {websiteAnalysisError}
              </small>
            )}
          </div>
          <label>
            <span>Business email domains <small>One per line</small></span>
            <textarea
              maxLength={2000}
              rows={2}
              value={form.email_domains}
              disabled={!canManage || busy || analyzingWebsite}
              onChange={(event) => update("email_domains", event.target.value)}
              placeholder={"example.com"}
            />
          </label>
        </div>
        {websiteAnalysis && (
          <div className="workspace-business-profile-analysis">
            {websiteAnalysisNotice && (
              <small role="status">{websiteAnalysisNotice}</small>
            )}
            <details className="workspace-business-profile-evidence">
              <summary>
                Review website evidence ({websiteAnalysis.evidence.length})
              </summary>
              <p>
                Source:{" "}
                <a href={websiteAnalysis.source} target="_blank" rel="noreferrer">
                  {websiteAnalysis.source}
                </a>
              </p>
              {websiteAnalysis.evidence.length > 0 && (
                <ul>
                  {websiteAnalysis.evidence.map((fact, index) => (
                    <li key={`${fact.field}-${index}`}>
                      <b>{fact.field}: {fact.value}</b>
                      <span>“{fact.excerpt}”</span>
                    </li>
                  ))}
                </ul>
              )}
              <small>{websiteAnalysis.limitations}</small>
            </details>
          </div>
        )}
        <label>
          <span>What your business does</span>
          <textarea
            maxLength={2000}
            rows={2}
            value={form.description}
            disabled={!canManage || busy || analyzingWebsite}
            onChange={(event) => update("description", event.target.value)}
            placeholder="Briefly describe your organization and industry."
          />
        </label>
        <label>
          <span>Products and services</span>
          <textarea
            maxLength={2000}
            rows={2}
            value={form.products_services}
            disabled={!canManage || busy || analyzingWebsite}
            onChange={(event) => update("products_services", event.target.value)}
            placeholder="What you sell or provide."
          />
        </label>
        <label>
          <span>How to distinguish your business from a lead</span>
          <textarea
            maxLength={1500}
            rows={2}
            value={form.lead_distinction_notes}
            disabled={!canManage || busy || analyzingWebsite}
            onChange={(event) => update("lead_distinction_notes", event.target.value)}
            placeholder="Add any important context about your organization and how it differs from prospective customers."
          />
        </label>
        <div className="workspace-business-profile-actions">
          {notice && <span role="status">{notice}</span>}
          {canManage && (
            <button
              className="primary-button"
              type="submit"
              disabled={!policy || busy || analyzingWebsite}
            >
              {busy ? "Saving…" : "Save business context"}
            </button>
          )}
        </div>
      </form>
    </div>
  );
}
