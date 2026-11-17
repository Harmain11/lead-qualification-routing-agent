import { useEffect, useMemo, useRef, useState } from "react";
import { getCommunicationPreferences, listCommunicationNotifications, type AssignmentNotification } from "../api";
import { playCommunicationSound } from "../communicationSound";
import type { LeadDetailSection } from "../leadSections";
import type { Lead } from "../types";

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
  assignmentNotifications,
  onOpenLead,
  onMarkAssignmentRead,
  canViewCommunication,
  workspaceId,
  onOpenCommunication,
}: {
  unseenLeadIds: string[];
  leads: Lead[];
  assignmentNotifications: AssignmentNotification[];
  onOpenLead: (leadId: string, section?: LeadDetailSection) => void;
  onMarkAssignmentRead: (notificationId: string) => void | Promise<void>;
  canViewCommunication: boolean;
  workspaceId: string;
  onOpenCommunication: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [communicationUnread, setCommunicationUnread] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const notificationCount = unseenLeadIds.length + assignmentNotifications.length + communicationUnread;
  const items = useMemo(() => {
    const byId = new Map(leads.map((lead) => [lead.id, lead]));
    return unseenLeadIds
      .map((id) => ({ id, lead: byId.get(id) }))
      .sort((left, right) =>
        leadTimestamp(right.lead) - leadTimestamp(left.lead),
      );
  }, [leads, unseenLeadIds]);

  useEffect(() => {
    let active = true;
    let initialized = false;
    let known = new Set<string>();
    let requestSequence = 0;
    setCommunicationUnread(0);
    if (!canViewCommunication || !workspaceId) return;
    const refresh = () => {
      const requestId = ++requestSequence;
      void Promise.all([listCommunicationNotifications(), getCommunicationPreferences()]).then(([items, preferences]) => {
      if (!active || requestId !== requestSequence) return;
      const newUnread = initialized && items.some((item) => !item.read_at && !known.has(item.id));
      known = new Set(items.map((item) => item.id));
      initialized = true;
      if (newUnread && preferences.sound_enabled) playCommunicationSound();
      setCommunicationUnread(items.filter((item) => !item.read_at).length);
      }).catch(() => undefined);
    };
    refresh();
    const interval = window.setInterval(refresh, 30_000);
    document.addEventListener("visibilitychange", refresh);
    window.addEventListener("communication-preferences-changed", refresh);
    return () => { active = false; window.clearInterval(interval); document.removeEventListener("visibilitychange", refresh); window.removeEventListener("communication-preferences-changed", refresh); };
  }, [canViewCommunication, workspaceId]);

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
        aria-label={notificationCount
          ? `Notifications, ${notificationCount} unread`
          : "Notifications"}
        aria-expanded={open}
        aria-controls="lead-notification-inbox"
        title="Notifications"
        onClick={() => setOpen((current) => !current)}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M18 9a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4" />
        </svg>
        {notificationCount > 0 && (
          <span className="lead-notification-count">
            {notificationCount > 99 ? "99+" : notificationCount}
          </span>
        )}
      </button>
      {open && (
        <section
          className="lead-notification-inbox"
          id="lead-notification-inbox"
          role="region"
          aria-label="Notification inbox"
        >
          <header>
            <div>
              <strong>Notifications</strong>
              <small>
                {assignmentNotifications.length} items need your attention
                {" · "}
                {unseenLeadIds.length} unopened leads
                {communicationUnread > 0 && ` · ${communicationUnread} communication alerts`}
              </small>
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
          {items.length || assignmentNotifications.length || communicationUnread ? (
            <ul>
              {communicationUnread > 0 && <li><button type="button" className="lead-notification-item" onClick={() => { setOpen(false); onOpenCommunication(); }}><span className="lead-notification-dot" aria-hidden="true" /><span className="lead-notification-copy"><strong>Communication</strong><small>{communicationUnread} unread {communicationUnread === 1 ? "alert" : "alerts"}</small></span><span aria-hidden="true">›</span></button></li>}
              {assignmentNotifications.map((notification) => (
                <li key={`assignment:${notification.id}`}>
                  <button
                    type="button"
                    className="lead-notification-item"
                    onClick={() => {
                      setOpen(false);
                      onOpenLead(
                        notification.lead_id,
                        notification.kind === "meeting_reply_draft" ? "email" : undefined,
                      );
                      void onMarkAssignmentRead(notification.id);
                    }}
                  >
                    <span className="lead-notification-dot" aria-hidden="true" />
                    <span className="lead-notification-copy">
                      <strong>
                        {notification.kind === "meeting_reply_draft"
                          ? "Meeting reply needs review"
                          : "Lead assigned to you"}
                      </strong>
                      <small>
                        {notification.message || `${notification.lead_name}${
                          notification.company_name
                            ? ` · ${notification.company_name}`
                            : ""
                        }`}
                      </small>
                      <small>
                        {new Date(notification.created_at).toLocaleString()}
                      </small>
                    </span>
                    <span aria-hidden="true">›</span>
                  </button>
                </li>
              ))}
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
                      {lead?.qualification?.icp_source === "not_configured" && (
                        <small className="lead-notification-icp-note">
                          ICP fit not scored. Add a custom ICP in Settings to enable fit scoring.
                        </small>
                      )}
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
