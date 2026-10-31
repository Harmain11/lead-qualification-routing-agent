import { useEffect, useMemo, useState } from "react";
import { createClientId } from "../clientIds";
import { Icon } from "../v3/ui/Icon";
import { OperationsDialog } from "../components/OperationsDialog";
import { opsGet, opsSave, type Company, type Contact } from "../operationsApi";
import { confirmAction } from "../v3/ui/confirm";
import { Button, Sheet } from "../v3/ui/primitives";
import type { ReactNode } from "react";
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
  linkedin_url: string;
  department: string;
  seniority: string;
  mobile_phone: string;
  preferred_channel: string;
  location: string;
  notes: string;
  tags: string;
  description: string;
  employees: string;
  annual_revenue: string;
  headquarters: string;
  founded_year: string;
};
const splitTags = (value: string) => value.split(",").map((tag) => tag.trim()).filter(Boolean);
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
  linkedin_url: "",
  department: "",
  seniority: "",
  mobile_phone: "",
  preferred_channel: "",
  location: "",
  notes: "",
  tags: "",
  description: "",
  employees: "",
  annual_revenue: "",
  headquarters: "",
  founded_year: "",
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
    const profile = (item.profile ?? {}) as Record<string, unknown>;
    const text = (key: string) => (typeof profile[key] === "string" ? (profile[key] as string) : typeof profile[key] === "number" ? String(profile[key]) : "");
    Object.assign(value, {
      linkedin_url: text("linkedin_url"),
      notes: text("notes"),
      tags: Array.isArray(profile.tags) ? (profile.tags as unknown[]).filter((tag) => typeof tag === "string").join(", ") : "",
    });
    if ("email" in item) {
      Object.assign(value, {
        email: item.email || "",
        company_id: item.company_id || "",
        phone: item.phone || "",
        job_title: item.job_title || "",
        timezone: item.timezone,
        department: text("department"),
        seniority: text("seniority"),
        mobile_phone: text("mobile_phone"),
        preferred_channel: text("preferred_channel"),
        location: text("location"),
      });
    } else {
      Object.assign(value, {
        description: text("description"),
        employees: text("employees"),
        annual_revenue: text("annual_revenue"),
        headquarters: text("headquarters"),
        founded_year: text("founded_year"),
        phone: text("phone"),
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
            profile: {
              linkedin_url: form.linkedin_url,
              department: form.department,
              seniority: form.seniority,
              mobile_phone: form.mobile_phone,
              preferred_channel: form.preferred_channel,
              location: form.location,
              notes: form.notes,
              tags: splitTags(form.tags),
            },
          }
        : {
            name: form.name,
            domain: form.domain.trim().toLowerCase() || null,
            website: form.website || null,
            website_reviewed: form.website_reviewed,
            industry: form.industry || null,
            profile: {
              description: form.description,
              employees: form.employees,
              annual_revenue: form.annual_revenue,
              headquarters: form.headquarters,
              founded_year: form.founded_year,
              phone: form.phone,
              linkedin_url: form.linkedin_url,
              notes: form.notes,
              tags: splitTags(form.tags),
            },
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
    const companyMissing = company
      ? [
          !company.domain && "company domain",
          !company.website && "official website",
          !company.industry && "industry",
        ].filter(Boolean) as string[]
      : [];
    return (
      <article className="records-card" key={item.id}>
        <div className="records-card-topline">
          <span className="records-avatar" aria-hidden="true">{initials(item.name)}</span>
          <button className="records-card-name" onClick={() => void open(item.id)}>
            {item.name}
          </button>
          {canEdit && mode !== "companies" && (
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
              {companyMissing.length > 0 && (
                <span className="records-missing-fields">Missing: {companyMissing.join(", ")}</span>
              )}
            </>
          )}
        </div>
        {mode === "companies" && (
          <div className="records-card-actions">
            {canEdit && (
              <button
                className="records-card-action"
                disabled={busy}
                aria-label={`Edit ${companyItem.name}`}
                onClick={() => edit(companyItem)}
              >
                Edit company
              </button>
            )}
          </div>
        )}
        <button className="records-card-footer" onClick={() => void open(item.id)}>
          View {recordLabel} <span aria-hidden="true">›</span>
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
                    <span aria-hidden="true">{expandedColumns[column.key] ? "–" : "+"}</span>
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
                    void confirmAction({
                      title: "Merge these contacts?",
                      message: "Lead touches move to the first person. Both email identities and consent histories stay recorded.",
                      confirmLabel: "Merge contacts",
                    }).then((confirmed) => {
                      if (!confirmed) return;
                      void act(async () => {
                        await opsSave(
                          `/v1/records/contacts/${row.right_id}/merge`,
                          { target_id: row.left_id, confirm: true },
                        );
                        setDuplicates((current) =>
                          current.filter((value) => value !== row),
                        );
                      });
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
        <Sheet
          title={`${companies.some((c) => c.id === form.id) || items.some((i) => i.id === form.id) ? "Edit" : "New"} ${mode === "contacts" ? "contact" : "company"}`}
          subtitle={mode === "contacts" ? "Everything Corral knows about this person. Only the name is required." : "Firmographics, research permission and notes for this account."}
          width={640}
          onClose={() => setForm(null)}
          footer={
            <div className="rec-footer">
              {error && <span role="alert" className="rec-error">{error}</span>}
              <Button kind="gray" onClick={() => setForm(null)}>Cancel</Button>
              <Button kind="filled" type="submit" form="record-form" disabled={busy || !form.name.trim()}>{busy ? "Saving…" : "Save"}</Button>
            </div>
          }
        >
          <form id="record-form" className="rec-form" onSubmit={(event) => { event.preventDefault(); void save(); }}>
            {notice && <p role="status" className="rec-notice">{notice}</p>}
            {mode === "contacts" ? (
              <>
                <fieldset className="rec-section">
                  <legend>Basics</legend>
                  <div className="rec-grid">
                    <RecField label="Full name" required><input required maxLength={255} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} autoComplete="off" /></RecField>
                    <RecField label="Job title"><input maxLength={255} value={form.job_title} onChange={(e) => setForm({ ...form, job_title: e.target.value })} placeholder="Head of Operations" /></RecField>
                    <RecField label="Department"><input maxLength={300} value={form.department} onChange={(e) => setForm({ ...form, department: e.target.value })} placeholder="Operations" /></RecField>
                    <RecField label="Seniority">
                      <select value={form.seniority} onChange={(e) => setForm({ ...form, seniority: e.target.value })}>
                        <option value="">Not set</option>
                        {["Individual contributor", "Manager", "Director", "VP", "C-level", "Owner or founder"].map((level) => <option key={level} value={level}>{level}</option>)}
                      </select>
                    </RecField>
                  </div>
                </fieldset>
                <fieldset className="rec-section">
                  <legend>How to reach them</legend>
                  <div className="rec-grid">
                    <RecField label="Email"><input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} placeholder="name@company.com" /></RecField>
                    <RecField label="Preferred channel">
                      <select value={form.preferred_channel} onChange={(e) => setForm({ ...form, preferred_channel: e.target.value })}>
                        <option value="">No preference</option>
                        <option value="email">Email</option><option value="phone">Phone call</option><option value="sms">Text message</option><option value="whatsapp">WhatsApp</option>
                      </select>
                    </RecField>
                    <RecField label="Phone"><input maxLength={64} value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} placeholder="+1 555 010 0100" inputMode="tel" /></RecField>
                    <RecField label="Mobile"><input maxLength={300} value={form.mobile_phone} onChange={(e) => setForm({ ...form, mobile_phone: e.target.value })} inputMode="tel" /></RecField>
                    <RecField label="LinkedIn" wide><input type="url" value={form.linkedin_url} onChange={(e) => setForm({ ...form, linkedin_url: e.target.value })} placeholder="https://www.linkedin.com/in/…" /></RecField>
                  </div>
                </fieldset>
                <fieldset className="rec-section">
                  <legend>Company and place</legend>
                  <div className="rec-grid">
                    <RecField label="Company">
                      <select value={form.company_id} onChange={(e) => setForm({ ...form, company_id: e.target.value })}>
                        <option value="">Not linked</option>
                        {companies.map((company) => <option value={company.id} key={company.id}>{company.name}</option>)}
                      </select>
                    </RecField>
                    <RecField label="Location"><input maxLength={300} value={form.location} onChange={(e) => setForm({ ...form, location: e.target.value })} placeholder="Austin, Texas" /></RecField>
                    <RecField label="Time zone" wide hint="Used to schedule calls and meetings at a sensible hour.">
                      <input list="rec-timezones" value={form.timezone} onChange={(e) => setForm({ ...form, timezone: e.target.value })} placeholder="America/Chicago" />
                    </RecField>
                  </div>
                </fieldset>
              </>
            ) : (
              <>
                <fieldset className="rec-section">
                  <legend>Basics</legend>
                  <div className="rec-grid">
                    <RecField label="Company name" required><input required maxLength={255} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} autoComplete="off" /></RecField>
                    <RecField label="Industry"><input value={form.industry} onChange={(e) => setForm({ ...form, industry: e.target.value })} placeholder="Software" /></RecField>
                    <RecField label="Domain" hint="Filled in from the website if you add one."><input value={form.domain} onChange={(e) => setForm({ ...form, domain: e.target.value })} placeholder="company.com" /></RecField>
                    <RecField label="Founded"><input inputMode="numeric" maxLength={4} value={form.founded_year} onChange={(e) => setForm({ ...form, founded_year: e.target.value })} placeholder="2014" /></RecField>
                    <RecField label="What they do" wide><textarea rows={3} maxLength={2000} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="A short description of the business." /></RecField>
                  </div>
                </fieldset>
                <fieldset className="rec-section">
                  <legend>Size and place</legend>
                  <div className="rec-grid">
                    <RecField label="Employees">
                      <select value={form.employees} onChange={(e) => setForm({ ...form, employees: e.target.value })}>
                        <option value="">Not set</option>
                        {["1-10", "11-50", "51-200", "201-500", "501-1,000", "1,001-5,000", "5,000+"].map((size) => <option key={size} value={size}>{size}</option>)}
                      </select>
                    </RecField>
                    <RecField label="Annual revenue">
                      <select value={form.annual_revenue} onChange={(e) => setForm({ ...form, annual_revenue: e.target.value })}>
                        <option value="">Not set</option>
                        {["Under $1M", "$1M-$10M", "$10M-$50M", "$50M-$250M", "$250M-$1B", "Over $1B"].map((size) => <option key={size} value={size}>{size}</option>)}
                      </select>
                    </RecField>
                    <RecField label="Headquarters"><input maxLength={300} value={form.headquarters} onChange={(e) => setForm({ ...form, headquarters: e.target.value })} placeholder="Austin, Texas" /></RecField>
                    <RecField label="Main phone"><input maxLength={64} value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} inputMode="tel" /></RecField>
                  </div>
                </fieldset>
                <fieldset className="rec-section">
                  <legend>Online</legend>
                  <div className="rec-grid">
                    <RecField label="Official website" hint="HTTPS only. Used for cited research.">
                      <input type="url" value={form.website} onChange={(e) => setForm({ ...form, website: e.target.value, website_reviewed: false })} placeholder="https://company.com/about" />
                    </RecField>
                    <RecField label="LinkedIn"><input type="url" value={form.linkedin_url} onChange={(e) => setForm({ ...form, linkedin_url: e.target.value })} placeholder="https://www.linkedin.com/company/…" /></RecField>
                  </div>
                  <label className="rec-switch">
                    <span><b>Allow Corral to research this company</b><small>Turn on after you have reviewed the official page. Suggestions wait in Intelligence for your approval.</small></span>
                    <input type="checkbox" role="switch" className="v-switch" checked={form.website_reviewed} disabled={!form.website} onChange={(e) => setForm({ ...form, website_reviewed: e.target.checked })} />
                  </label>
                </fieldset>
              </>
            )}
            <fieldset className="rec-section">
              <legend>Notes and labels</legend>
              <div className="rec-grid">
                <RecField label="Notes" wide><textarea rows={3} maxLength={2000} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} placeholder="Anything the team should know." /></RecField>
                <RecField label="Tags" wide hint="Separate with commas, for example: partner, renewal-q4."><input value={form.tags} onChange={(e) => setForm({ ...form, tags: e.target.value })} /></RecField>
              </div>
            </fieldset>
            <datalist id="rec-timezones">{timezoneOptions().map((zone) => <option key={zone} value={zone} />)}</datalist>
          </form>
        </Sheet>
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

function timezoneOptions(): string[] {
  try {
    const supported = (Intl as unknown as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf;
    return supported ? supported("timeZone") : ["UTC", "America/New_York", "America/Chicago", "America/Los_Angeles", "Europe/London", "Europe/Berlin", "Asia/Karachi", "Asia/Dubai", "Asia/Singapore", "Australia/Sydney"];
  } catch {
    return ["UTC"];
  }
}

function RecField({ label, hint, required, wide, children }: { label: string; hint?: string; required?: boolean; wide?: boolean; children: ReactNode }) {
  return (
    <label className="rec-field" data-wide={wide || undefined}>
      <span>{label}{required && <i aria-hidden="true"> *</i>}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}
