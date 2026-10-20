import { useEffect, useMemo, useRef, useState } from "react";
import type { Lead } from "../types";
import "./LeadNotificationCenter.css";

function leadTimestamp(lead?: Lead): number {
  if (!lead) return 0;
  const timestamp = Date.parse(lead.created_at);
  return Number.isFinite(timestamp) ? timestamp : 0;
}

function leadDate(lead: Lead): string {
  const timestamp = leadTimestamp(lead);
  return timestamp
    ? new Date(timestamp).toLocaleString(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      })
    : "New arrival";
}

export function LeadNotificationCenter({
  unseenLeadIds,
  leads,
  onOpenLead,
}: {
  unseenLeadIds: string[];
  leads: Lead[];
  onOpenLead: (leadId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const items = useMemo(() => {
    const byId = new Map(leads.map((lead) => [lead.id, lead]));
    return unseenLeadIds
      .map((id) => ({ id, lead: byId.get(id) }))
      .sort((left, right) =>
        leadTimestamp(right.lead) - leadTimestamp(left.lead),
      );
  }, [leads, unseenLeadIds]);

  useEffect(() => {
    if (!open) return;
    const closeOnOutsideClick = (event: PointerEvent) => {
      if (event.target instanceof Node && !rootRef.current?.contains(event.target)) {
        setOpen(false);
      }
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", closeOnOutsideClick);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsideClick);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  return (
    <div className="lead-notification-center" ref={rootRef}>
      <button
        className="lead-notification-trigger"
        type="button"
        aria-label={unseenLeadIds.length
          ? `Notifications, ${unseenLeadIds.length} new leads`
          : "Notifications"}
        aria-expanded={open}
        aria-controls="lead-notification-inbox"
        title="New lead notifications"
        onClick={() => setOpen((current) => !current)}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M18 9a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4" />
        </svg>
        {unseenLeadIds.length > 0 && (
          <span className="lead-notification-count">
            {unseenLeadIds.length > 99 ? "99+" : unseenLeadIds.length}
          </span>
        )}
      </button>
      {open && (
        <section
          className="lead-notification-inbox"
          id="lead-notification-inbox"
          role="region"
          aria-label="New lead inbox"
        >
          <header>
            <div>
              <strong>New leads</strong>
              <small>{unseenLeadIds.length} unopened</small>
            </div>
            <button
              type="button"
              className="lead-notification-close"
              aria-label="Close notifications"
              onClick={() => setOpen(false)}
            >
              ×
            </button>
          </header>
          {items.length ? (
            <ul>
              {items.map(({ id, lead }) => (
                <li key={id}>
                  <button
                    type="button"
                    className="lead-notification-item"
                    onClick={() => {
                      setOpen(false);
                      onOpenLead(id);
                    }}
                  >
                    <span className="lead-notification-dot" aria-hidden="true" />
                    <span className="lead-notification-copy">
                      <strong>{lead?.full_name || "New lead"}</strong>
                      {lead?.company_name && <small>{lead.company_name}</small>}
                      <small>{lead ? leadDate(lead) : "Open to review this lead"}</small>
                    </span>
                    <span aria-hidden="true">›</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="lead-notification-empty">
              You’re all caught up. New lead arrivals will appear here.
            </p>
          )}
        </section>
      )}
    </div>
  );
}
