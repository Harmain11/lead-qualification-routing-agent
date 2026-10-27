import { useEffect, useMemo, useState } from "react";
import { createClientId } from "../clientIds";
import { Icon } from "../v3/ui/Icon";
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
  const [notice, setNotice] = useState("");
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
  const [viewMode, setViewMode] = useState<"board" | "table">(
    mode === "companies" ? "board" : "table",
  );
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [savedView, setSavedView] = useState<"all" | "mine">("all");
  const [expandedColumns, setExpandedColumns] = useState<Record<string, boolean>>({});
  useEffect(() => {
    setViewMode(mode === "companies" ? "board" : "table");
    setSavedView("all");
    setExpandedColumns({});
  }, [mode]);
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
  async function act(task: () => Promise<unknown>, successMessage?: string) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await task();
      setRevision((value) => value + 1);
      if (successMessage) setNotice(successMessage);
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
    setError("");
    setNotice("");
    setDetail(null);
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
  function enrichCompany(company: Company) {
    if (!company.website || !company.website_reviewed) {
      if (!canEdit) {
        setError(
          "An editor must add the official company page and mark it reviewed before AI enrichment can run.",
        );
        return;
      }
      edit(company);
      setNotice(
        company.website
          ? "Review the official page, mark it reviewed, and save before requesting AI enrichment."
          : "Add the official company page and mark it reviewed before requesting AI enrichment.",
      );
      return;
    }
    void act(
      () => opsSave(`/v1/intelligence/companies/${company.id}/research`, {}),
      "Enrichment draft created. Review its cited suggestions in Intelligence before applying them.",
    );
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
            domain: form.domain.trim().toLowerCase() || null,
            website: form.website || null,
            website_reviewed: form.website_reviewed,
            industry: form.industry || null,
          };
    await act(
      async () => {
        await opsSave(`/v1/records/${mode}/${form.id}`, body, "PUT");
        setForm(null);
        setDetail(null);
      },
      mode === "companies" ? "Company details saved." : "Contact details saved.",
    );
  }
  const companyById = useMemo(
    () => new Map(companies.map((company) => [company.id, company])),
    [companies],
  );
  const boardColumns = useMemo(() => {
    if (mode === "companies") {
      const companiesInView = items as Company[];
      return [
        {
          key: "reviewed",
          label: "Reviewed",
          description: "Approved company pages",
          items: companiesInView.filter((item) => item.website_reviewed),
        },
        {
          key: "needs-review",
          label: "Needs review",
          description: "Has a website to verify",
          items: companiesInView.filter(
            (item) => !item.website_reviewed && Boolean(item.website),
          ),
        },
        {
          key: "needs-enrichment",
          label: "Needs enrichment",
          description: "Add details manually or enrich a reviewed website",
          items: companiesInView.filter(
            (item) => !item.website_reviewed && !item.website,
          ),
        },
      ];
    }
    const contactsInView = items as Contact[];
    return [
      {
        key: "linked",
        label: "Linked",
        description: "Connected to a company",
        items: contactsInView.filter((item) => Boolean(item.company_id)),
      },
      {
        key: "needs-company",
        label: "Needs company",
        description: "Link a company record",
        items: contactsInView.filter((item) => !item.company_id),
      },
    ];
  }, [items, mode]);
  const recordLabel = mode === "contacts" ? "contact" : "company";
  const recordLabelPlural = mode === "contacts" ? "contacts" : "companies";
  function initials(name: string) {
    return name
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase())
      .join("");
  }
  function renderBoardCard(item: Contact | Company) {
    const contact = "email" in item ? item : null;
    const company = contact
      ? companyById.get(contact.company_id || "")
      : (item as Company);
    const companyItem = item as Company;
    return (
      <article className="records-card" key={item.id}>
        <div className="records-card-topline">
          <span className="records-avatar" aria-hidden="true">{initials(item.name)}</span>
          <button className="records-card-name" onClick={() => void open(item.id)}>
            {item.name}
          </button>
          {canEdit && (
            <button className="records-card-menu" aria-label={`Edit ${item.name}`} onClick={() => edit(item)}>•••</button>
          )}
        </div>
        <div className="records-card-meta">
          {contact ? (
            <>
              <span>{contact.job_title || "Contact"}</span>
              <span>{contact.email || "No email"}</span>
              <span>{contact.phone || "No phone"}</span>
              <span>{company?.name || "Unlinked"}</span>
            </>
          ) : (
            <>
              <span>{companyItem.domain || "No domain"}</span>
              <span>{companyItem.industry || "Industry not set"}</span>
              <span className={companyItem.website_reviewed ? "records-status is-good" : "records-status"}>
                {companyItem.website_reviewed
                  ? "Website reviewed"
                  : companyItem.website
                    ? "Needs website review"
                    : "Needs enrichment"}
              </span>
            </>
          )}
        </div>
        {mode === "companies" && (
          <div className="records-card-actions">
            {canEdit && (
              <button
                className="records-card-action"
                disabled={busy}
                aria-label={`Enter or edit ${companyItem.name} details manually`}
                onClick={() => edit(companyItem)}
              >
                Manual entry
              </button>
            )}
            {canResearch && (
              <button
                className="records-card-action is-enrich"
                disabled={busy}
                onClick={() => enrichCompany(companyItem)}
              >
                {companyItem.website_reviewed && companyItem.website
                  ? "Enrich with AI"
                  : companyItem.website
                    ? "Review page to enrich"
                    : "Add website to enrich"}
              </button>
            )}
          </div>
        )}
        <button className="records-card-footer" onClick={() => void open(item.id)}>
          View {recordLabel} <span aria-hidden="true">→</span>
        </button>
      </article>
    );
  }
  return (
    <div className="page-view ops-page records-view">
      <header className="records-head">
        <div className="records-head-copy">
          <span className="eyebrow">People and companies</span>
          <h1>{mode === "contacts" ? "Contacts" : "Companies"}</h1>
          <p>
            {mode === "contacts"
              ? "Keep every person, conversation and lead touch in one focused workspace."
              : "Bring every linked contact, lead and recorded win into one company workspace."}
          </p>
        </div>
        {canEdit && (
          <div className="records-head-actions" role="group" aria-label={`${recordLabel} actions`}>
            <button className="records-add-button" onClick={() => { setError(""); setNotice(""); setForm(blank()); }}>
              Add {recordLabel} <span aria-hidden="true">+</span>
            </button>
          </div>
        )}
      </header>
      <nav className="records-view-tabs" aria-label={`${recordLabelPlural} views`}>
        <button className={savedView === "all" ? "is-active" : ""} onClick={() => setSavedView("all")} aria-pressed={savedView === "all"}>
          All {recordLabelPlural} <strong>{total}</strong>
        </button>
        <button className={savedView === "mine" ? "is-active" : ""} onClick={() => setSavedView("mine")} aria-pressed={savedView === "mine"}>
          My {recordLabelPlural}
        </button>
      </nav>
      {error && (
        <p role="alert" className="team-alert">
          {error}
        </p>
      )}
      {notice && !form && !detail && (
        <p role="status" className="records-notice">{notice}</p>
      )}
      <div className="records-toolbar">
        <label className="records-search">
          <span className="sr-only">Search {recordLabelPlural}</span>
          <input
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setOffset(0);
            }}
            placeholder={
              mode === "contacts" ? "Search name, email or phone" : "Search company or domain"
            }
          />
        </label>
        <button className={`records-filter-button ${filtersOpen ? "is-active" : ""}`} onClick={() => setFiltersOpen((value) => !value)}>
          <Icon name="filter" size={16} /> Filter{mode === "contacts" && sort !== "name" ? " · 1" : ""}
        </button>
        <button className="records-filter-button" onClick={() => setFiltersOpen(true)}>
          <Icon name="chevronUpDown" size={16} /> Sort by
        </button>
        <div className="records-toolbar-spacer" />
        <div className="records-view-switcher" role="group" aria-label="View mode">
          <button className={viewMode === "board" ? "is-active" : ""} onClick={() => setViewMode("board")} aria-pressed={viewMode === "board"}><Icon name="layers" size={16} /> Board</button>
          <button className={viewMode === "table" ? "is-active" : ""} onClick={() => setViewMode("table")} aria-pressed={viewMode === "table"}><Icon name="dashboard" size={16} /> Table</button>
        </div>
        <button className="records-icon-button" aria-label="Refresh records" title="Refresh" onClick={() => setRevision((value) => value + 1)}><Icon name="refresh" size={18} /></button>
      </div>
      {filtersOpen && (
        <div className="records-filter-panel">
          <label>
            Sort records
            <select value={sort} onChange={(event) => { setSort(event.target.value); setOffset(0); }}>
              <option value="name">Name</option>
              {mode === "contacts" && <><option value="recent">Recently updated</option><option value="company">Company</option></>}
            </select>
          </label>
          {canEdit && <button className="button-quiet" disabled={busy} onClick={() => void act(() => opsSave("/v1/records/backfill", {}))}>Link existing lead touches</button>}
          {mode === "contacts" && <button className="secondary-button" onClick={() => void act(async () => setDuplicates((await opsGet<{ items: typeof duplicates }>("/v1/records/duplicates")).items))}>Review duplicates</button>}
          <button className="records-filter-close" onClick={() => setFiltersOpen(false)}>Done</button>
        </div>
      )}
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
      ) : viewMode === "board" ? (
        <section className="records-board" aria-label={`${recordLabelPlural} board`}>
          {boardColumns.map((column) => (
            <div className="records-column" key={column.key}>
              <div className="records-column-head">
                <div><h2>{column.label}</h2><p>{column.description}</p></div>
                <span>{column.items.length}</span>
              </div>
              <div className="records-column-body">
                {(expandedColumns[column.key] ? column.items : column.items.slice(0, 3)).map((item) => renderBoardCard(item))}
                {column.items.length > 3 && (
                  <button
                    className="records-see-more"
                    onClick={() => setExpandedColumns((current) => ({ ...current, [column.key]: !current[column.key] }))}
                    aria-expanded={Boolean(expandedColumns[column.key])}
                  >
                    {expandedColumns[column.key]
                      ? "Show less"
                      : `See more (${column.items.length - 3})`}
                    <span aria-hidden="true">{expandedColumns[column.key] ? "↑" : "↓"}</span>
                  </button>
                )}
                {canEdit && <button className="records-add-inline" onClick={() => { setError(""); setNotice(""); setForm(blank()); }}>+ {mode === "contacts" ? "Contact" : "Company"}</button>}
              </div>
            </div>
          ))}
        </section>
      ) : (
        <div className="ops-table-wrap" role="region" aria-label="Table; scroll horizontally to view all columns" tabIndex={0}>
          <table className="ops-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>{mode === "contacts" ? "Email" : "Domain"}</th>
                {mode === "contacts" && <th>Phone</th>}
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
                  {mode === "contacts" && <td>{"email" in item ? item.phone || "—" : "—"}</td>}
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
            {notice && <p role="status" className="records-notice">{notice}</p>}
            {mode === "companies" && (
              <p className="records-form-help">
                Enter company details manually, or add its official page and mark it reviewed to request a cited AI enrichment draft. Suggestions stay in Intelligence for your approval.
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
                        domain: event.target.value,
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
          {notice && <p role="status" className="records-notice">{notice}</p>}
          <div className="ops-panel">
            <p>{detail.contact?.email || detail.company?.domain}</p>
            {detail.company && (
              <p>
                Company: {detail.company.name} ·{" "}
                {detail.company.industry || "Industry unknown"}
              </p>
            )}
            {mode === "companies" && detail.company && (
              <div className="records-detail-actions">
                {canEdit && (
                  <button
                    className="secondary-button"
                    disabled={busy}
                    onClick={() => edit(detail.company!)}
                  >
                    Enter details manually
                  </button>
                )}
                {canResearch && (
                  <button
                    className="primary-button"
                    disabled={busy}
                    onClick={() => enrichCompany(detail.company!)}
                  >
                    {detail.company.website_reviewed && detail.company.website
                      ? "Enrich with AI"
                      : detail.company.website
                        ? "Review page to enrich"
                        : "Add website to enrich"}
                  </button>
                )}
              </div>
            )}
            {mode === "companies" && detail.company && canResearch &&
              (!detail.company.website || !detail.company.website_reviewed) && (
                <p className="ops-muted">
                  Add the official HTTPS page and mark it reviewed before requesting AI enrichment. Suggestions appear in Intelligence for approval.
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
