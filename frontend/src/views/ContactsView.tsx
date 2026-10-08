import { useEffect, useMemo, useState } from "react";
import { getLeadPipelineHistory, listLeads } from "../api";
import type { Lead, LeadPipelineEvent } from "../types";
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
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<LeadPipelineEvent[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const contacts = useMemo(() => makeContacts(leads), [leads]);
  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return contacts;
    return contacts.filter((contact) => [
      contact.name,
      contact.email,
      ...contact.leads.flatMap((lead) => [lead.company_name, lead.industry ?? "", contactDomain(lead.email)]),
    ].join(" ").toLowerCase().includes(query));
  }, [contacts, search]);
  const selected = contacts.find((contact) => contact.key === selectedKey) ?? null;

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
  }, []);

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
      try {
        const recentLeads = selected!.leads.slice(0, 20);
        const entries = await Promise.all(recentLeads.map(async (lead) => {
          const events = await getLeadPipelineHistory(lead.id, controller.signal);
          return events;
        }));
        if (active) {
          setHistory(entries.flat().sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at)));
        }
      } catch {
        if (active && !controller.signal.aborted) setHistory([]);
      } finally {
        if (active) setHistoryLoading(false);
      }
    }
    void loadHistory();
    return () => { active = false; controller.abort(); };
  }, [selectedKey]);

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
      {error && <div className="contacts-error" role="alert"><span>{error}</span><button onClick={() => window.location.reload()}>Retry</button></div>}
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
                  {historyLoading ? <p className="contact-muted">Loading activity…</p> : (
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
