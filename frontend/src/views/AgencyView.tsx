import { useEffect, useState } from "react";
import {
  opsGet,
  opsSave,
  type Policy,
  type PolicyResponse,
} from "../operationsApi";
type Client = {
  workspace_id: string;
  name: string;
  role: string;
  leads: number;
  wins: number;
  current: boolean;
};
export function AgencyView({
  canManage,
  onSwitch,
}: {
  canManage: boolean;
  onSwitch: (id: string) => Promise<void>;
}) {
  const [clients, setClients] = useState<Client[]>([]);
  const [policy, setPolicy] = useState<PolicyResponse | null>(null);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  useEffect(() => {
    let active = true;
    opsGet<{ items: Client[] }>("/v1/ops/agency")
      .then((data) => {
        if (active) setClients(data.items);
      })
      .catch((reason) => {
        if (active) setError(reason.message);
      });
    opsGet<PolicyResponse>("/v1/ops/policy")
      .then((data) => {
        if (active) setPolicy(data);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);
  function branding(patch: Partial<Policy>) {
    if (policy)
      setPolicy({
        ...policy,
        configuration: { ...policy.configuration, ...patch },
      });
  }
  async function save() {
    if (!policy) return;
    setBusy(true);
    setError("");
    try {
      const result = await opsSave<{ configuration: Policy; version: number }>(
        "/v1/ops/policy",
        { configuration: policy.configuration, version: policy.version },
        "PUT",
      );
      setPolicy({ ...policy, ...result });
      setNotice("Branding saved. Reload to apply the workspace identity.");
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Unable to save branding",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="page-view ops-page">
      <header className="ops-page-head">
        <div>
          <span className="eyebrow">Authorized client workspaces</span>
          <h1>Agency overview</h1>
          <p>
            Manage the client workspaces your account can access. Every client
            keeps its own permissions and records.
          </p>
        </div>
      </header>
      {error && (
        <p role="alert" className="team-alert">
          {error}
        </p>
      )}
      <label>
        Find a client
        <input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </label>
      <div className="ops-card-grid">
        {clients
          .filter((client) =>
            client.name.toLowerCase().includes(search.toLowerCase()),
          )
          .map((client) => (
            <article className="ops-panel" key={client.workspace_id}>
              <h2>{client.name}</h2>
              <p>{client.role.replaceAll("_", " ")}</p>
              <div className="ops-metrics">
                <span>
                  <b>{client.leads}</b>Leads
                </span>
                <span>
                  <b>{client.wins}</b>Recorded wins
                </span>
              </div>
              <button
                className="secondary-button"
                disabled={client.current || busy}
                onClick={() => {
                  setBusy(true);
                  void onSwitch(client.workspace_id)
                    .catch((reason) => setError(reason.message))
                    .finally(() => setBusy(false));
                }}
              >
                {client.current ? "Current workspace" : "Open client workspace"}
              </button>
            </article>
          ))}
      </div>
      {policy && (
        <section className="ops-panel">
          <h2>Current workspace branding</h2>
          <form
            className="ops-form"
            onSubmit={(event) => {
              event.preventDefault();
              void save();
            }}
          >
            <fieldset
              disabled={!canManage || busy}
              className="ops-policy-fieldset"
            >
              <label>
                Product name
                <input
                  maxLength={100}
                  value={policy.configuration.brand_name || ""}
                  onChange={(event) =>
                    branding({ brand_name: event.target.value || null })
                  }
                  placeholder="Corral"
                />
              </label>
              <label>
                Logo HTTPS URL
                <input
                  type="url"
                  value={policy.configuration.brand_logo_url || ""}
                  onChange={(event) =>
                    branding({ brand_logo_url: event.target.value || null })
                  }
                />
              </label>
              <label>
                Accent color
                <input
                  type="color"
                  value={policy.configuration.brand_accent}
                  onChange={(event) =>
                    branding({ brand_accent: event.target.value })
                  }
                />
              </label>
              <label>
                Custom domain
                <input
                  value={policy.configuration.custom_domain || ""}
                  onChange={(event) =>
                    branding({
                      custom_domain: event.target.value.toLowerCase() || null,
                    })
                  }
                  placeholder="sales.yourcompany.com"
                />
              </label>
              <p className="ops-muted">
                A custom domain needs DNS and TLS configured on your hosting
                provider. Saving the domain here does not create that hosting
                configuration.
              </p>
            </fieldset>
            {canManage && (
              <button className="primary-button" disabled={busy}>
                Save branding
              </button>
            )}
            {notice && <p role="status">{notice}</p>}
          </form>
        </section>
      )}
      <section className="ops-panel">
        <h2>Zapier / Make connection</h2>
        <p>
          Corral's API and signed intake can be used with HTTP modules.
          Integration templates are in the development checkout. Marketplace
          publication requires the vendor accounts and approval.
        </p>
        <a
          href={import.meta.env.DEV ? "http://localhost:8001/docs" : "/docs"}
          target="_blank"
          rel="noreferrer"
        >
          Open local API reference
        </a>
      </section>
    </div>
  );
}
