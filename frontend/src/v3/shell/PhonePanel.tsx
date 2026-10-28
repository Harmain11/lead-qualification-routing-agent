import { useEffect, useMemo, useRef, useState } from "react";
import { DirectDialer, type DirectDialerConnection } from "../../components/DirectDialer";
import type { Lead } from "../../types";
import { Icon } from "../ui/Icon";
import { Button, IconButton, Monogram } from "../ui/primitives";

/**
 * The phone, one click away on every screen. When a browser phone (CloudTalk,
 * Aircall, RingCentral) is connected, the shell shows that provider's own phone
 * instead and keeps it loaded so a call survives navigation. Without one, this
 * panel offers Corral's direct dialer (Twilio and other direct providers) and a
 * fast way to open any lead's call screen, where consent checks run.
 */
export function PhonePanel({
  open,
  onClose,
  connections,
  leads,
  canCall,
  canViewLeads,
  canEditLeads,
  canSetUp,
  preferredConnectionId,
  onPreferredConnectionChange,
  onCallLead,
  onSetUp,
}: {
  open: boolean;
  onClose: () => void;
  connections: DirectDialerConnection[];
  leads: Lead[];
  canCall: boolean;
  canViewLeads: boolean;
  canEditLeads: boolean;
  canSetUp: boolean;
  preferredConnectionId: string;
  onPreferredConnectionChange: (id: string) => void;
  onCallLead: (leadId: string) => void;
  onSetUp: () => void;
}) {
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<"leads" | "keypad">(connections.length ? "keypad" : "leads");
  const panelRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    panelRef.current?.querySelector<HTMLElement>("input, button")?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  useEffect(() => {
    if (!connections.length) setTab("leads");
  }, [connections.length]);

  const callable = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return leads
      .filter((lead) => lead.phone)
      .filter((lead) => !needle || `${lead.full_name} ${lead.company_name} ${lead.phone}`.toLowerCase().includes(needle))
      .slice(0, 30);
  }, [leads, query]);
  const withoutPhone = leads.filter((lead) => !lead.phone).length;

  return (
    <aside ref={panelRef} className="p-panel" data-open={open || undefined} aria-label="Phone" hidden={!open}>
      <header className="p-head">
        <span className="p-head-icon"><Icon name="phone" size={18} /></span>
        <div>
          <h2>Phone</h2>
          <p>{connections.length ? `${connections.length} calling connection${connections.length === 1 ? "" : "s"}` : "No phone connected yet"}</p>
        </div>
        <IconButton icon="close" label="Close phone" onClick={onClose} size={16} />
      </header>

      {connections.length > 0 && (
        <div className="p-tabs" role="tablist" aria-label="Phone">
          <button type="button" role="tab" aria-selected={tab === "keypad"} onClick={() => setTab("keypad")}><Icon name="keypad" size={15} />Keypad</button>
          <button type="button" role="tab" aria-selected={tab === "leads"} onClick={() => setTab("leads")}><Icon name="leads" size={15} />Leads</button>
        </div>
      )}

      <div className="p-body">
        {tab === "keypad" && connections.length > 0 && (
          <div className="v-legacy-block p-dialer">
            <DirectDialer
              connections={connections}
              canCall={canCall}
              canViewLeads={canViewLeads}
              canEditLeads={canEditLeads}
              onOpenLead={onCallLead}
              preferredConnectionId={preferredConnectionId}
              onPreferredConnectionChange={onPreferredConnectionChange}
              phoneCallActive={false}
              ringCentralEmbedReady={false}
            />
          </div>
        )}

        {tab === "leads" && (
          <>
            {connections.length === 0 && (
              <div className="p-setup">
                <span className="p-setup-art" aria-hidden="true"><Icon name="keypad" size={26} /></span>
                <b>Call from inside Corral</b>
                <p>Connect CloudTalk, Aircall or RingCentral for a phone that stays with you on every screen, or Twilio for one-click dialing. Calls are logged on the lead with consent checks.</p>
                {canSetUp && <Button kind="filled" size="sm" onClick={onSetUp}>Set Up Calling</Button>}
              </div>
            )}
            <label className="p-search">
              <Icon name="search" size={15} />
              <span className="v-sr">Find a lead to call</span>
              <input type="search" placeholder="Find a lead to call" value={query} onChange={(event) => setQuery(event.target.value)} />
            </label>
            {callable.length ? (
              <ul className="p-leads">
                {callable.map((lead) => (
                  <li key={lead.id}>
                    <Monogram text={lead.company_name || lead.full_name} size={32} />
                    <span className="p-lead-text">
                      <b>{lead.full_name || lead.company_name}</b>
                      <small>{lead.company_name ? `${lead.company_name} · ` : ""}<span className="num">{lead.phone}</span></small>
                    </span>
                    <button type="button" className="p-call" onClick={() => onCallLead(lead.id)} aria-label={`Call ${lead.full_name || lead.company_name}`}>
                      <Icon name="phone" size={15} />
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="p-none">{query ? "No lead with a phone number matches." : "No leads have a phone number yet."}</p>
            )}
            {withoutPhone > 0 && !query && <p className="p-footnote">{withoutPhone} lead{withoutPhone === 1 ? " has" : "s have"} no phone number on record.</p>}
          </>
        )}
      </div>
    </aside>
  );
}
