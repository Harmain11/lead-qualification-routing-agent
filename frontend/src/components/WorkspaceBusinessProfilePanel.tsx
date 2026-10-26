import { useEffect, useState, type FormEvent } from "react";
import {
  opsGet,
  opsSave,
  type Policy,
  type PolicyResponse,
  type WorkspaceBusinessProfile,
} from "../operationsApi";

type BusinessProfileForm = {
  business_name: string;
  alternate_names: string;
  website: string;
  email_domains: string;
  description: string;
  products_services: string;
  lead_distinction_notes: string;
  email_signature: string;
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
  email_signature: "",
};

const productServicePresets = [
  "Software / SaaS",
  "Consulting",
  "Professional services",
  "Managed services",
  "Marketing and advertising",
  "Design and creative services",
  "Training and education",
  "Financial services",
  "Healthcare services",
  "Logistics and fulfillment",
  "Manufacturing",
  "Hardware and equipment",
  "Retail and e-commerce",
  "Consumer products",
  "Subscription services",
];

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
  email_signature: profile.email_signature ?? "",
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
  const [presetService, setPresetService] = useState("");
  const [presetServiceMessage, setPresetServiceMessage] = useState<
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

  const addProductServicePreset = (value: string) => {
    if (!value) return;
    const existing = lines(form.products_services);
    if (existing.some((item) => item.toLocaleLowerCase() === value.toLocaleLowerCase())) {
      setPresetServiceMessage(`${value} is already in the list.`);
    } else {
      const next = [...existing, value].join("\n");
      if (next.length > 2000) {
        setPresetServiceMessage("The list is full. Remove an item before adding another.");
      } else {
        update("products_services", next);
        setPresetServiceMessage(`${value} added.`);
      }
    }
    setPresetService("");
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
      email_signature: form.email_signature.trim(),
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
        "Business context saved. New AI drafts can use this workspace identity and email signature.",
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
        Add the details AI needs to recognize your business. Website suggestions
        stay editable until you save.
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
        <section className="workspace-business-profile-section">
          <div className="workspace-business-profile-section-heading">
            <h3>Organization</h3>
            <p>Names that help AI recognize your business.</p>
          </div>
          <div className="workspace-business-profile-field-grid">
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
                placeholder="Trading names or product brands"
              />
            </label>
          </div>
        </section>

        <section className="workspace-business-profile-section">
          <div className="workspace-business-profile-section-heading">
            <h3>Website and email</h3>
            <p>Analyze a public page to get cited profile suggestions.</p>
          </div>
          <div className="workspace-business-profile-field-grid">
            <label>
              <span>Website</span>
              <div className="workspace-business-profile-website-input">
                <input
                  type="url"
                  maxLength={500}
                  value={form.website}
                  disabled={!canManage || busy || analyzingWebsite}
                  onChange={(event) => update("website", event.target.value)}
                  placeholder="https://example.com"
                />
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
              </div>
              <small className="workspace-business-profile-hint">
                Only this page is read. Review suggestions before saving.
              </small>
              {websiteAnalysisError && (
                <small className="workspace-business-profile-analysis-error" role="alert">
                  {websiteAnalysisError}
                </small>
              )}
            </label>
            <label>
              <span>Business email domains <small>One per line</small></span>
              <textarea
                maxLength={2000}
                rows={2}
                value={form.email_domains}
                disabled={!canManage || busy || analyzingWebsite}
                onChange={(event) => update("email_domains", event.target.value)}
                placeholder="company.com"
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
        </section>

        <section className="workspace-business-profile-section">
          <div className="workspace-business-profile-section-heading">
            <h3>What you offer</h3>
            <p>Keep these details brief so they are easy to scan.</p>
          </div>
          <div className="workspace-business-profile-field-grid">
            <label>
              <span>What your business does</span>
              <textarea
                maxLength={2000}
                rows={2}
                value={form.description}
                disabled={!canManage || busy || analyzingWebsite}
                onChange={(event) => update("description", event.target.value)}
                placeholder="One short sentence about your organization."
              />
            </label>
            <div className="workspace-business-profile-service-field">
              <label>
                <span>Products and services <small>One per line</small></span>
                <textarea
                  maxLength={2000}
                  rows={2}
                  value={form.products_services}
                  disabled={!canManage || busy || analyzingWebsite}
                  onChange={(event) => {
                    update("products_services", event.target.value);
                    setPresetServiceMessage(null);
                  }}
                  placeholder="What you sell or provide."
                />
              </label>
              <div className="workspace-business-profile-service-add">
                <select
                  aria-label="Add a common product or service"
                  value={presetService}
                  disabled={!canManage || busy || analyzingWebsite}
                  onChange={(event) => addProductServicePreset(event.target.value)}
                >
                  <option value="">Add from common options…</option>
                  {productServicePresets.map((item) => (
                    <option key={item} value={item}>{item}</option>
                  ))}
                </select>
                <small>Choose an option to add it, or type your own.</small>
              </div>
              {presetServiceMessage && (
                <small className="workspace-business-profile-preset-status" role="status">
                  {presetServiceMessage}
                </small>
              )}
            </div>
          </div>
        </section>

        <section className="workspace-business-profile-section">
          <div className="workspace-business-profile-section-heading">
            <h3>Lead context <small>Optional</small></h3>
            <p>Clarify how your business differs from prospective customers.</p>
          </div>
          <label>
            <span>How to distinguish your business from a lead</span>
            <textarea
              maxLength={1500}
              rows={2}
              value={form.lead_distinction_notes}
              disabled={!canManage || busy || analyzingWebsite}
              onChange={(event) => update("lead_distinction_notes", event.target.value)}
              placeholder="For example, we provide services to clinics; we are not a clinic."
            />
          </label>
        </section>

        <section className="workspace-business-profile-section">
          <div className="workspace-business-profile-section-heading">
            <h3>Email signature <small>Optional</small></h3>
            <p>Used on AI-generated follow-up emails. Leave blank to use the business name when available.</p>
          </div>
          <label>
            <span>Workspace sender signature</span>
            <textarea
              maxLength={1200}
              rows={4}
              value={form.email_signature}
              disabled={!canManage || busy || analyzingWebsite}
              onChange={(event) => update("email_signature", event.target.value)}
              placeholder={"Best regards,\nYour name\nYour title · Business name"}
            />
          </label>
        </section>

        <div className="workspace-business-profile-actions">
          {notice ? (
            <span role="status">{notice}</span>
          ) : (
            <small className="workspace-business-profile-save-hint">
              Suggestions are applied only when you save.
            </small>
          )}
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
