import { useEffect, useState } from "react";
import { createClientId } from "../clientIds";
import { OperationsDialog } from "../components/OperationsDialog";
import { opsGet, opsSave, type Company, type Contact } from "../operationsApi";
type Touch = {
  id: string;
  source: string;
  received_at: string;
  name?: string;
  score?: number;
  stage?: string;
};
type Detail = {
  contact?: Contact;
  company?: Company;
  contacts?: Contact[];
  touches: Touch[];
  lead_count?: number;
  won?: number;
  revenue_by_currency?: Record<string, number>;
  truncated?: boolean;
};
type Form = {
  id: string;
  name: string;
  email: string;
  company_id: string;
  phone: string;
  job_title: string;
  timezone: string;
  domain: string;
  website: string;
  website_reviewed: boolean;
  industry: string;
};
const blank = (): Form => ({
  id: createClientId(),
  name: "",
  email: "",
  company_id: "",
  phone: "",
  job_title: "",
  timezone: "UTC",
  domain: "",
  website: "",
  website_reviewed: false,
  industry: "",
});
export function RecordsView({
  mode,
  onInspect,
  canEdit,
  canResearch,
}: {
  mode: "contacts" | "companies";
  onInspect: (id: string) => void;
  canEdit: boolean;
  canResearch: boolean;
}) {
  const [items, setItems] = useState<(Contact | Company)[]>([]);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState<Form | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [duplicates, setDuplicates] = useState<
    {
      left_id: string;
      right_id: string;
      left_name: string;
      right_name: string;
      reason: string;
      confidence: number;
    }[]
  >([]);
  const [sort, setSort] = useState("name");
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    const query = new URLSearchParams({
      search,
      offset: String(offset),
      limit: "50",
      ...(mode === "contacts" ? { sort } : {}),
    });
    opsGet<{ items: (Contact | Company)[]; total: number }>(
      `/v1/records/${mode}?${query}`,
      controller.signal,
    )
      .then((data) => {
        setItems(data.items);
        setTotal(data.total);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(reason.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [mode, search, offset, revision, sort]);
  useEffect(() => {
    const controller = new AbortController();
    opsGet<{ items: Company[] }>(
      "/v1/records/companies?limit=100",
      controller.signal,
    )
      .then((data) => setCompanies(data.items))
      .catch(() => {});
    return () => controller.abort();
  }, [revision, mode]);
  useEffect(() => {
    function target() {
      const value = window.sessionStorage.getItem("corral-record-target");
      if (!value) return;
      try {
        const record = JSON.parse(value);
        if (
          record.kind === (mode === "contacts" ? "Contact" : "Company") &&
          typeof record.id === "string"
        ) {
          window.sessionStorage.removeItem("corral-record-target");
          void opsGet<Detail>(
            "/v1/records/" + mode + "/" + encodeURIComponent(record.id),
          )
            .then(setDetail)
            .catch((reason) => setError(reason.message));
        }
      } catch {
        window.sessionStorage.removeItem("corral-record-target");
      }
    }
    target();
    window.addEventListener("corral-record-target", target);
    return () => window.removeEventListener("corral-record-target", target);
  }, [mode]);
  async function act(task: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await task();
      setRevision((value) => value + 1);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Unable to complete action",
      );
    } finally {
      setBusy(false);
    }
  }
  async function open(id: string) {
    await act(async () =>
      setDetail(await opsGet<Detail>(`/v1/records/${mode}/${id}`)),
    );
  }
  function edit(item: Contact | Company) {
    const value = blank();
    value.id = item.id;
    value.name = item.name;
    if ("email" in item) {
      Object.assign(value, {
        email: item.email || "",
        company_id: item.company_id || "",
        phone: item.phone || "",
        job_title: item.job_title || "",
        timezone: item.timezone,
      });
    } else {
      Object.assign(value, {
        domain: item.domain || "",
        website: item.website || "",
        website_reviewed: item.website_reviewed,
        industry: item.industry || "",
      });
    }
    setForm(value);
  }
  async function save() {
    if (!form) return;
    const body =
      mode === "contacts"
        ? {
            name: form.name,
            email: form.email || null,
            company_id: form.company_id || null,
            phone: form.phone || null,
            job_title: form.job_title || null,
            timezone: form.timezone,
          }
        : {
            name: form.name,
            domain: form.domain || null,
            website: form.website || null,
            website_reviewed: form.website_reviewed,
            industry: form.industry || null,
          };
    await act(async () => {
      await opsSave(`/v1/records/${mode}/${form.id}`, body, "PUT");
      setForm(null);
      setDetail(null);
    });
  }
  return (
    <div className="page-view ops-page">
      <header className="ops-page-head">
        <div>
          <span className="eyebrow">People and companies</span>
          <h1>{mode === "contacts" ? "Contacts" : "Companies"}</h1>
          <p>
            {mode === "contacts"
              ? "Keep a person record across lead touches and channels."
              : "Bring every linked contact, lead and recorded win into one company view."}
          </p>
        </div>
        {canEdit && (
          <div
            className="records-header-actions"
            role="group"
            aria-label={`${mode === "contacts" ? "Contact" : "Company"} actions`}
          >
            <button
              className="primary-button"
              onClick={() => {
                setError("");
                setForm(blank());
              }}
            >
              Add {mode === "contacts" ? "contact" : "company"}
            </button>
          </div>
        )}
      </header>
      {error && (
        <p role="alert" className="team-alert">
          {error}
        </p>
      )}
      <div className="ops-toolbar">
        <label>
          Search
          <input
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setOffset(0);
            }}
            placeholder={
              mode === "contacts" ? "Name or email" : "Company or domain"
            }
          />
        </label>
        {mode === "contacts" && (
          <label>
            Sort
            <select
              value={sort}
              onChange={(event) => {
                setSort(event.target.value);
                setOffset(0);
              }}
            >
              <option value="name">Name</option>
              <option value="recent">Recently updated</option>
              <option value="company">Company</option>
            </select>
          </label>
        )}
        <button
          className="button-quiet"
          onClick={() => setRevision((value) => value + 1)}
        >
          Refresh
        </button>
        {canEdit && (
          <button
            className="button-quiet"
            disabled={busy}
            onClick={() => void act(() => opsSave("/v1/records/backfill", {}))}
          >
            Link existing lead touches
          </button>
        )}
        {mode === "contacts" && (
          <button
            className="secondary-button"
            onClick={() =>
              void act(async () =>
                setDuplicates(
                  (
                    await opsGet<{ items: typeof duplicates }>(
                      "/v1/records/duplicates",
                    )
                  ).items,
                ),
              )
            }
          >
            Review duplicates
          </button>
        )}
      </div>
      {loading ? (
        <p role="status">Loading records…</p>
      ) : !items.length ? (
        <section className="ops-empty">
          <h3>No {mode} yet</h3>
          <p>
            New lead intake creates linked records. Add a record here or link
            your existing lead touches.
          </p>
        </section>
      ) : (
        <div className="ops-table-wrap" role="region" aria-label="Table; scroll horizontally to view all columns" tabIndex={0}>
          <table className="ops-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>{mode === "contacts" ? "Email" : "Domain"}</th>
                <th>{mode === "contacts" ? "Company" : "Industry"}</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td>
                    <button
                      className="ops-link"
                      onClick={() => void open(item.id)}
                    >
                      {item.name}
                    </button>
                  </td>
                  <td>
                    {"email" in item ? item.email || "—" : item.domain || "—"}
                  </td>
                  <td>
                    {"email" in item
                      ? companies.find(
                          (company) => company.id === item.company_id,
                        )?.name || "Unlinked"
                      : item.industry || "Unknown"}
                  </td>
                  <td>
                    {canEdit && (
                      <button
                        className="button-quiet"
                        onClick={() => edit(item)}
                      >
                        Edit
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="ops-pagination">
        <span>
          {total ? offset + 1 : 0}–{Math.min(offset + 50, total)} of {total}
        </span>
        <div className="ops-actions">
        <button
          className="button-quiet"
          disabled={!offset}
          onClick={() => setOffset(Math.max(0, offset - 50))}
        >
          Previous
        </button>
        <button
          className="button-quiet"
          disabled={offset + 50 >= total}
          onClick={() => setOffset(offset + 50)}
        >
          Next
        </button>
        </div>
      </div>
      {duplicates.length > 0 && (
        <section className="ops-panel">
          <h2>Possible duplicate people</h2>
          <p>
            Compare identities before merging. Similar names at one company can
            be different people.
          </p>
          {duplicates.map((row) => (
            <article className="ops-note" key={row.left_id + row.right_id}>
              <strong>
                {row.left_name} / {row.right_name}
              </strong>
              <p>
                {row.reason} · {Math.round(row.confidence * 100)}% similarity
              </p>
              {canEdit && (
                <button
                  className="secondary-button"
                  onClick={() => {
                    if (
                      window.confirm(
                        "Merge these contact records? Lead touches move to the first person. Both email identities and consent histories remain recorded.",
                      )
                    )
                      void act(async () => {
                        await opsSave(
                          `/v1/records/contacts/${row.right_id}/merge`,
                          { target_id: row.left_id, confirm: true },
                        );
                        setDuplicates((current) =>
                          current.filter((value) => value !== row),
                        );
                      });
                  }}
                >
                  Merge after review
                </button>
              )}
            </article>
          ))}
        </section>
      )}
      {form && (
        <OperationsDialog
          title={mode === "contacts" ? "Contact details" : "Company details"}
          className="record-editor-dialog"
          onClose={() => setForm(null)}
        >
          <form
            className="ops-form record-editor-form"
            onSubmit={(event) => {
              event.preventDefault();
              void save();
            }}
          >
            {error && (
              <p role="alert" className="team-alert">
                {error}
              </p>
            )}
            <label>
              Name
              <input
                required
                maxLength={255}
                value={form.name}
                onChange={(event) =>
                  setForm({ ...form, name: event.target.value })
                }
              />
            </label>
            {mode === "contacts" ? (
              <>
                <label>
                  Email
                  <input
                    type="email"
                    value={form.email}
                    onChange={(event) =>
                      setForm({ ...form, email: event.target.value })
                    }
                  />
                </label>
                <label>
                  Company
                  <select
                    value={form.company_id}
                    onChange={(event) =>
                      setForm({ ...form, company_id: event.target.value })
                    }
                  >
                    <option value="">Unlinked</option>
                    {companies.map((company) => (
                      <option value={company.id} key={company.id}>
                        {company.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Phone
                  <input
                    maxLength={64}
                    value={form.phone}
                    onChange={(event) =>
                      setForm({ ...form, phone: event.target.value })
                    }
                  />
                </label>
                <label>
                  Job title
                  <input
                    maxLength={255}
                    value={form.job_title}
                    onChange={(event) =>
                      setForm({ ...form, job_title: event.target.value })
                    }
                  />
                </label>
                <label>
                  Timezone
                  <input
                    value={form.timezone}
                    onChange={(event) =>
                      setForm({ ...form, timezone: event.target.value })
                    }
                    placeholder="Asia/Karachi"
                  />
                </label>
              </>
            ) : (
              <>
                <label>
                  Company domain
                  <input
                    value={form.domain}
                    onChange={(event) =>
                      setForm({
                        ...form,
                        domain: event.target.value.toLowerCase(),
                      })
                    }
                    placeholder="company.com"
                  />
                </label>
                <label>
                  Official HTTPS page
                  <input
                    type="url"
                    value={form.website}
                    onChange={(event) =>
                      setForm({
                        ...form,
                        website: event.target.value,
                        website_reviewed: false,
                      })
                    }
                    placeholder="https://company.com/about"
                  />
                </label>
                <label className="ops-check">
                  <input
                    type="checkbox"
                    checked={form.website_reviewed}
                    disabled={!form.website}
                    onChange={(event) =>
                      setForm({
                        ...form,
                        website_reviewed: event.target.checked,
                      })
                    }
                  />
                  I reviewed this official company page and allow Corral to
                  research it.
                </label>
                <label>
                  Industry
                  <input
                    value={form.industry}
                    onChange={(event) =>
                      setForm({ ...form, industry: event.target.value })
                    }
                  />
                </label>
              </>
            )}
            <button className="primary-button" disabled={busy}>
              {busy ? "Saving…" : "Save record"}
            </button>
          </form>
        </OperationsDialog>
      )}
      {detail && (
        <OperationsDialog
          title={
            detail.contact?.name || detail.company?.name || "Record history"
          }
          onClose={() => setDetail(null)}
        >
          {error && (
            <p role="alert" className="team-alert">
              {error}
            </p>
          )}
          <div className="ops-panel">
            <p>{detail.contact?.email || detail.company?.domain}</p>
            {detail.company && (
              <p>
                Company: {detail.company.name} ·{" "}
                {detail.company.industry || "Industry unknown"}
              </p>
            )}
            {mode === "companies" && detail.company && canResearch && (
              <button
                className="primary-button"
                disabled={busy || !detail.company.website_reviewed}
                onClick={() =>
                  void act(() =>
                    opsSave(
                      `/v1/intelligence/companies/${detail.company!.id}/research`,
                      {},
                    ),
                  )
                }
              >
                Research reviewed website
              </button>
            )}
            {mode === "companies" &&
              detail.company &&
              !detail.company.website_reviewed && (
                <p className="ops-muted">
                  Edit this company and review its official page to enable
                  research. Results appear in Intelligence for review.
                </p>
              )}
            {detail.contacts && (
              <div>
                <h3>People</h3>
                {detail.contacts.map((contact) => (
                  <p key={contact.id}>
                    {contact.name} · {contact.email || "No email"}
                  </p>
                ))}
              </div>
            )}
            {detail.revenue_by_currency && (
              <div>
                <h3>Recorded wins: {detail.won}</h3>
                {Object.entries(detail.revenue_by_currency).map(
                  ([currency, value]) => (
                    <p key={currency}>
                      {currency} {value.toLocaleString()}
                    </p>
                  ),
                )}
              </div>
            )}
          </div>
          <section className="ops-panel">
            <h3>Lead touches</h3>
            {detail.truncated && <p>Showing the latest 500 touches.</p>}
            {detail.touches.map((touch) => (
              <article className="ops-note" key={touch.id}>
                <button
                  className="ops-link"
                  onClick={() => {
                    setDetail(null);
                    onInspect(touch.id);
                  }}
                >
                  {touch.name || touch.source} ·{" "}
                  {new Date(touch.received_at).toLocaleString()}
                </button>
                <p>
                  {touch.source}
                  {touch.score != null ? ` · Score ${touch.score}` : ""}
                  {touch.stage ? ` · ${touch.stage}` : ""}
                </p>
              </article>
            ))}
            {!detail.touches.length && <p>No lead touches are linked yet.</p>}
          </section>
        </OperationsDialog>
      )}
    </div>
  );
}
