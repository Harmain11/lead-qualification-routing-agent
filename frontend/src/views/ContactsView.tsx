import { useEffect,useMemo,useState } from "react";
import { getLeadPipelineHistory,listLeads } from "../api";
import type { Lead,LeadPipelineEvent } from "../types";
import "./ContactsView.css";

type Contact = {
  key: string;
  email: string;
  name: string;
  leads: Lead[];
};

function contactDomain(email: string) {
  const at = email.lastIndexOf("@");
  return at >= 0 ? email.slice(at + 1).trim().toLowerCase() : "—";
}

function makeContacts(leads: Lead[]): Contact[] {
  const groups = new Map<string, Contact>();
  for (const lead of leads) {
    const email = lead.email.trim();
    const key = email ? email.toLowerCase() : `lead:${lead.id}`;
    const found = groups.get(key);
    if (found) {
      found.leads.push(lead);
    } else {
      groups.set(key, {
        key,
        email,
        name: lead.full_name || email || "Unknown contact",
        leads: [lead],
      });
    }
  }
  return [...groups.values()].map((contact) => ({
    ...contact,
    leads: contact.leads.sort((a, b) => Date.parse(b.received_at) - Date.parse(a.received_at)),
  })).sort((a, b) => Date.parse(b.leads[0]?.received_at ?? "") - Date.parse(a.leads[0]?.received_at ?? ""));
}

function displayDate(value: string | null | undefined) {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "—" : parsed.toLocaleString();
}

export function ContactsView({ onInspectLead }: { onInspectLead: (leadId: string) => void }) {
  const [leads, setLeads] = useState<Lead[]>([]);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [industryFilter, setIndustryFilter] = useState("");
  const [companyFilter, setCompanyFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [sortBy, setSortBy] = useState("recent");
  const [loading, setLoading] = useState(true);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<LeadPipelineEvent[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyAttempt, setHistoryAttempt] = useState(0);
  const contacts = useMemo(() => makeContacts(leads), [leads]);
  const filterOptions = useMemo(() => ({
    industries: [...new Set(leads.map((lead) => lead.industry?.trim()).filter((value): value is string => Boolean(value)))].sort((a, b) => a.localeCompare(b)),
    companies: [...new Set(leads.map((lead) => lead.company_name?.trim()).filter((value): value is string => Boolean(value)))].sort((a, b) => a.localeCompare(b)),
    sources: [...new Set(leads.map((lead) => lead.source_detail?.trim() || lead.source.replaceAll("_", " ")).filter(Boolean))].sort((a, b) => a.localeCompare(b)),
  }), [leads]);
  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    const from = dateFrom ? new Date(`${dateFrom}T00:00:00`).getTime() : null;
    const to = dateTo ? new Date(`${dateTo}T23:59:59.999`).getTime() : null;
    const matches = contacts.filter((contact) => {
      const searchable = [contact.name, contact.email, ...contact.leads.flatMap((lead) => [lead.company_name, lead.industry ?? "", lead.source_detail ?? "", lead.source, contactDomain(lead.email)])].join(" ").toLowerCase();
      if (query && !searchable.includes(query)) return false;
      if (industryFilter && !contact.leads.some((lead) => lead.industry?.trim() === industryFilter)) return false;
      if (companyFilter && !contact.leads.some((lead) => lead.company_name?.trim() === companyFilter)) return false;
      if (sourceFilter && !contact.leads.some((lead) => (lead.source_detail?.trim() || lead.source.replaceAll("_", " ")) === sourceFilter)) return false;
      const latestDate = Date.parse(contact.leads[0]?.received_at ?? "");
      if (from !== null && (!Number.isFinite(latestDate) || latestDate < from)) return false;
      if (to !== null && (!Number.isFinite(latestDate) || latestDate > to)) return false;
      return true;
    });
    const compareText = (a: string, b: string) => a.localeCompare(b, undefined, { sensitivity: "base" });
    return matches.sort((a, b) => {
      const latestA = Date.parse(a.leads[0]?.received_at ?? "");
      const latestB = Date.parse(b.leads[0]?.received_at ?? "");
      if (sortBy === "name") return compareText(a.name, b.name);
      if (sortBy === "company") return compareText(a.leads[0]?.company_name ?? "", b.leads[0]?.company_name ?? "");
      if (sortBy === "industry") return compareText(a.leads.find((lead) => lead.industry)?.industry ?? "", b.leads.find((lead) => lead.industry)?.industry ?? "");
      if (sortBy === "source") return compareText(a.leads[0]?.source_detail || a.leads[0]?.source || "", b.leads[0]?.source_detail || b.leads[0]?.source || "");
      return latestB - latestA;
    });
  }, [contacts, search, industryFilter, companyFilter, sourceFilter, dateFrom, dateTo, sortBy]);
  const selected = contacts.find((contact) => contact.key === selectedKey) ?? null;
  const hasFilters = Boolean(search || industryFilter || companyFilter || sourceFilter || dateFrom || dateTo);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const allLeads: Lead[] = [];
        let offset = 0;
        let hasMore = true;
        while (hasMore) {
          const page = await listLeads({ limit: 100, offset }, controller.signal);
          allLeads.push(...page.items);
          if (allLeads.length > 100_000) throw new Error("This workspace has too many records to load into the contact directory at once.");
          hasMore = page.has_more && page.items.length > 0;
          offset = page.offset + page.items.length;
        }
        if (active) setLeads(allLeads);
      } catch (loadError) {
        if (active && !controller.signal.aborted) {
          setError(loadError instanceof Error ? loadError.message : "Contacts could not be loaded.");
        }
      } finally {
        if (active) setLoading(false);
      }
    }
    void load();
    return () => { active = false; controller.abort(); };
  }, [loadAttempt]);

  useEffect(() => {
    if (!selected) {
      setHistory([]);
      return;
    }
    const controller = new AbortController();
    let active = true;
    async function loadHistory() {
      setHistoryLoading(true);
      setHistory([]);
      setHistoryError(null);
      try {
        const recentLeads = selected!.leads.slice(0, 20);
        const entries = await Promise.all(recentLeads.map(async (lead) => {
          const events = await getLeadPipelineHistory(lead.id, controller.signal);
          return events;
        }));
        if (active) {
          setHistory(entries.flat().sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at)));
        }
      } catch (loadError) {
        if (active && !controller.signal.aborted) {
          setHistoryError(loadError instanceof Error ? loadError.message : "Interaction history could not be loaded.");
        }
      } finally {
        if (active) setHistoryLoading(false);
      }
    }
    void loadHistory();
    return () => { active = false; controller.abort(); };
  }, [selectedKey, historyAttempt]);

  return (
    <section className="contacts-view" aria-labelledby="contacts-title">
      <header className="contacts-heading">
        <div>
          <span className="eyebrow">Workspace directory</span>
          <h1 id="contacts-title">Contacts</h1>
          <p>One contact record per email, with company details and recent lead activity.</p>
        </div>
        <div className="contacts-count"><strong>{contacts.length.toLocaleString()}</strong><span>unique contacts</span></div>
      </header>
      <div className="contacts-search">
        <label htmlFor="contacts-search">Search contacts</label>
        <input id="contacts-search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Name, email, company, domain or industry" />
      </div>
      <div className="contacts-filters" aria-label="Contact filters and sorting">
        <div className="contacts-filter-grid">
          <label><span>Industry</span><select value={industryFilter} onChange={(event) => setIndustryFilter(event.target.value)}><option value="">All industries</option>{filterOptions.industries.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
          <label><span>Company</span><select value={companyFilter} onChange={(event) => setCompanyFilter(event.target.value)}><option value="">All companies</option>{filterOptions.companies.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
          <label><span>Source</span><select value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value)}><option value="">All sources</option>{filterOptions.sources.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
          <label><span>Received from</span><input type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} /></label>
          <label><span>Received to</span><input type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} /></label>
          <label><span>Sort contacts</span><select value={sortBy} onChange={(event) => setSortBy(event.target.value)}><option value="recent">Most recent</option><option value="name">Name</option><option value="company">Company</option><option value="industry">Industry</option><option value="source">Source</option></select></label>
        </div>
        <div className="contacts-filter-meta"><span>{filtered.length.toLocaleString()} of {contacts.length.toLocaleString()} contacts</span>{hasFilters && <button type="button" onClick={() => { setSearch(""); setIndustryFilter(""); setCompanyFilter(""); setSourceFilter(""); setDateFrom(""); setDateTo(""); }}>Clear filters</button>}</div>
      </div>
      {error && <div className="contacts-error" role="alert"><span>{error}</span><button onClick={() => setLoadAttempt((attempt) => attempt + 1)}>Retry</button></div>}
      {loading ? <div className="contacts-empty" role="status">Loading workspace contacts…</div> : !error && contacts.length === 0 ? (
        <div className="contacts-empty"><strong>No contacts yet</strong><span>Contacts appear here as leads and CRM records arrive in this workspace.</span></div>
      ) : !error && filtered.length === 0 ? (
        <div className="contacts-empty"><strong>No matches</strong><span>Try a different name, email, company, or industry.</span></div>
      ) : !error && (
        <div className="contacts-layout">
          <div className="contacts-list" role="list" aria-label="Contacts">
            {filtered.map((contact) => {
              const latest = contact.leads[0];
              return (
                <button className={`contact-row ${selectedKey === contact.key ? "selected" : ""}`} key={contact.key} onClick={() => setSelectedKey(contact.key)} role="listitem">
                  <span className="contact-person"><strong>{contact.name}</strong><small>{contact.email}</small></span>
                  <span><strong>{latest.company_name || "—"}</strong><small>{latest.industry || "Industry not set"}</small></span>
                  <span><strong>{contactDomain(contact.email)}</strong><small>{contact.leads.length} interaction{contact.leads.length === 1 ? "" : "s"}</small></span>
                  <time dateTime={latest.received_at}>{displayDate(latest.received_at)}</time>
                </button>
              );
            })}
          </div>
          <aside className="contact-profile" aria-label="Contact details">
            {selected ? (
              <>
                <div className="contact-profile-head">
                  <span className="contact-avatar" aria-hidden="true">{selected.name.trim().slice(0, 1).toUpperCase() || "?"}</span>
                  <div><span className="eyebrow">Contact</span><h2>{selected.name}</h2></div>
                </div>
                <dl className="contact-facts">
                  <div><dt>Email</dt><dd><a href={`mailto:${selected.email}`}>{selected.email || "—"}</a></dd></div>
                  <div><dt>Email domain</dt><dd>{contactDomain(selected.email)}</dd></div>
                  <div><dt>Company</dt><dd>{selected.leads[0]?.company_name || "—"}</dd></div>
                  <div><dt>Industry</dt><dd>{selected.leads.find((lead) => lead.industry)?.industry || "—"}</dd></div>
                  <div><dt>Phone</dt><dd>{selected.leads.find((lead) => lead.phone)?.phone || "—"}</dd></div>
                  <div><dt>Role</dt><dd>{selected.leads.find((lead) => lead.job_title)?.job_title || "—"}</dd></div>
                </dl>
                <div className="contact-interactions">
                  <div className="contact-section-title"><h3>Interaction history</h3><span>{selected.leads.length} linked record{selected.leads.length === 1 ? "" : "s"}</span></div>
                   {historyLoading ? <p className="contact-muted" role="status">Loading activity…</p> : historyError ? <div className="contact-history-error" role="alert"><span>{historyError}</span><button type="button" onClick={() => setHistoryAttempt((attempt) => attempt + 1)}>Retry history</button></div> : (
                    <ol className="contact-timeline">
                      {selected.leads.slice(0, 20).map((lead) => (
                        <li key={`lead-${lead.id}`}>
                          <time dateTime={lead.received_at}>{displayDate(lead.received_at)}</time>
                          <strong>{lead.source_detail || lead.source.replaceAll("_", " ")}</strong>
                          <span>{lead.product_or_service || "Lead received"}</span>
                          {lead.message && <p>{lead.message}</p>}
                          <button onClick={() => onInspectLead(lead.id)}>Open full lead record →</button>
                        </li>
                      ))}
                      {history.map((event) => (
                        <li key={`event-${event.id}`}>
                          <time dateTime={event.created_at}>{displayDate(event.created_at)}</time>
                          <strong>{event.stage.replaceAll("_", " ")}</strong>
                          <span>{event.status.replaceAll("_", " ")}</span>
                        </li>
                      ))}
                      {!selected.leads.length && !history.length && <li className="contact-muted">No interaction history available.</li>}
                    </ol>
                  )}
                  {selected.leads.length > 20 && <p className="contact-muted">Showing the latest 20 records.</p>}
                </div>
              </>
            ) : (
              <div className="contact-profile-empty"><strong>Select a contact</strong><span>Company details and interaction history will appear here.</span></div>
            )}
          </aside>
        </div>
      )}
    </section>
  );
}
