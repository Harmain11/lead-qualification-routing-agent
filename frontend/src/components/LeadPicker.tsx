import { useEffect, useState } from "react";
import { listLeads } from "../api";
import type { Lead } from "../types";
import { Banner, Monogram } from "../v3/ui/primitives";
import { Icon } from "../v3/ui/Icon";

/** Search-as-you-type lead list used inside sheets (calls, coaching, linking). */
export function LeadPicker({ onPick, requirePhone = false, placeholder, initialSearch = "" }: { onPick: (lead: Lead) => void; requirePhone?: boolean; placeholder: string; initialSearch?: string }) {
  const [search, setSearch] = useState(initialSearch);
  const [leads, setLeads] = useState<Lead[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    const timeout = window.setTimeout(() => {
      listLeads({ limit: 20, search: search.trim() || undefined }, controller.signal)
        .then((result) => { if (!controller.signal.aborted) { setLeads(result.items); setError(""); } })
        .catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Leads couldn't be searched."); })
        .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, 180);
    return () => { window.clearTimeout(timeout); controller.abort(); };
  }, [search]);
  return (
    <div className="calls-picker">
      <label className="calls-search">
        <Icon name="search" size={15} />
        <span className="v-sr">{placeholder}</span>
        <input type="search" autoFocus value={search} onChange={(event) => setSearch(event.target.value)} placeholder={placeholder} />
      </label>
      {error && <Banner tone="danger" title={error} />}
      {loading && leads.length === 0 ? <p className="mailbox-empty" role="status">Searching…</p> : leads.length === 0 ? <p className="mailbox-empty">No matching leads.</p> : (
        <ul className="calls-picker-list">
          {leads.map((lead) => {
            const disabled = requirePhone && !lead.phone;
            return <li key={lead.id}>
              <button type="button" disabled={disabled} onClick={() => onPick(lead)} title={disabled ? "Add a phone number to this lead first" : undefined}>
                <Monogram text={lead.company_name || lead.full_name} size={30} />
                <span><b>{lead.full_name || lead.company_name}</b><small>{[lead.company_name, lead.phone || (requirePhone ? "No phone number" : lead.email)].filter(Boolean).join(" · ")}</small></span>
                <Icon name="chevronRight" size={14} />
              </button>
            </li>;
          })}
        </ul>
      )}
    </div>
  );
}
