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
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
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
  };

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
            disabled={!canManage || busy}
            onChange={(event) => update("business_name", event.target.value)}
            placeholder="Your organization’s name"
          />
        </label>
        <label>
          <span>Other names or brands <small>One per line</small></span>
          <textarea
            maxLength={2000}
            rows={3}
            value={form.alternate_names}
            disabled={!canManage || busy}
            onChange={(event) => update("alternate_names", event.target.value)}
            placeholder={"Trading names, brands, or former names"}
          />
        </label>
        <div className="workspace-business-profile-row">
          <label>
            <span>Website</span>
            <input
              type="url"
              maxLength={500}
              value={form.website}
              disabled={!canManage || busy}
              onChange={(event) => update("website", event.target.value)}
              placeholder="https://example.com"
            />
          </label>
          <label>
            <span>Business email domains <small>One per line</small></span>
            <textarea
              maxLength={2000}
              rows={2}
              value={form.email_domains}
              disabled={!canManage || busy}
              onChange={(event) => update("email_domains", event.target.value)}
              placeholder={"example.com"}
            />
          </label>
        </div>
        <label>
          <span>What your business does</span>
          <textarea
            maxLength={2000}
            rows={3}
            value={form.description}
            disabled={!canManage || busy}
            onChange={(event) => update("description", event.target.value)}
            placeholder="Briefly describe your organization and industry."
          />
        </label>
        <label>
          <span>Products and services</span>
          <textarea
            maxLength={2000}
            rows={3}
            value={form.products_services}
            disabled={!canManage || busy}
            onChange={(event) => update("products_services", event.target.value)}
            placeholder="What you sell or provide."
          />
        </label>
        <label>
          <span>How to distinguish your business from a lead</span>
          <textarea
            maxLength={1500}
            rows={3}
            value={form.lead_distinction_notes}
            disabled={!canManage || busy}
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
              disabled={!policy || busy}
            >
              {busy ? "Saving…" : "Save business context"}
            </button>
          )}
        </div>
      </form>
    </div>
  );
}
