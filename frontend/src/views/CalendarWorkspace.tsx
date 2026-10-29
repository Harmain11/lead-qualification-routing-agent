import { useEffect, useMemo, useState, type FormEvent } from "react";
import { createCalendarEvent, deleteCalendarEvent, listCalendarEvents } from "../api";
import type { Lead, WorkspaceCalendarEvent } from "../types";
import { Icon } from "../v3/ui/Icon";
import "./calendarWorkspace.css";
import { confirmAction } from "../v3/ui/confirm";

function dayKey(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function calendarDays(month: Date): Date[] {
  const first = new Date(month.getFullYear(), month.getMonth(), 1);
  first.setDate(first.getDate() - (first.getDay() + 6) % 7);
  return Array.from({ length: 42 }, (_, index) => new Date(first.getFullYear(), first.getMonth(), first.getDate() + index));
}

function localInput(day: string, hour: number): string {
  return `${day}T${String(hour).padStart(2, "0")}:00`;
}

function eventDay(event: WorkspaceCalendarEvent): string {
  return dayKey(new Date(event.start_at));
}

function eventTime(event: WorkspaceCalendarEvent): string {
  return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date(event.start_at));
}

function sourceName(source: WorkspaceCalendarEvent["source"]): string {
  return source === "google_calendar" ? "Google Calendar" : source === "calendly" ? "Calendly" : "Manual";
}

export function CalendarWorkspace({ leads, canEdit, onOpenLead, onOpenIntegrations }: {
  leads: Lead[];
  canEdit: boolean;
  onOpenLead: (leadId: string) => void;
  onOpenIntegrations: () => void;
}) {
  const today = dayKey(new Date());
  const [month, setMonth] = useState(() => new Date(new Date().getFullYear(), new Date().getMonth(), 1));
  const [selectedDay, setSelectedDay] = useState(today);
  const [events, setEvents] = useState<WorkspaceCalendarEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [revision, setRevision] = useState(0);
  const [editing, setEditing] = useState(false);
  const [kind, setKind] = useState<WorkspaceCalendarEvent["kind"]>("meeting");
  const [title, setTitle] = useState("");
  const [start, setStart] = useState(localInput(today, 10));
  const [end, setEnd] = useState(localInput(today, 11));
  const [location, setLocation] = useState("");
  const [notes, setNotes] = useState("");
  const [leadId, setLeadId] = useState("");
  const [filter, setFilter] = useState<"all" | WorkspaceCalendarEvent["kind"]>("all");
  const days = useMemo(() => calendarDays(month), [month]);
  const firstDay = dayKey(days[0]);
  const lastDay = dayKey(days[days.length - 1]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    listCalendarEvents(firstDay, lastDay, controller.signal)
      .then((rows) => { if (!controller.signal.aborted) setEvents(rows); })
      .catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Calendar events could not be loaded."); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [firstDay, lastDay, revision]);

  const filtered = filter === "all" ? events : events.filter((event) => event.kind === filter);
  const byDay = useMemo(() => {
    const map = new Map<string, WorkspaceCalendarEvent[]>();
    for (const event of filtered) {
      const key = eventDay(event);
      map.set(key, [...(map.get(key) ?? []), event]);
    }
    return map;
  }, [filtered]);
  const agenda = byDay.get(selectedDay) ?? [];

  function moveMonth(offset: number) {
    const next = new Date(month.getFullYear(), month.getMonth() + offset, 1);
    setMonth(next);
    setSelectedDay(dayKey(next));
  }

  function openNewEvent() {
    setStart(localInput(selectedDay, 10));
    setEnd(localInput(selectedDay, 11));
    setKind("meeting");
    setTitle("");
    setLocation("");
    setNotes("");
    setLeadId("");
    setNotice("");
    setEditing(true);
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    const from = new Date(start);
    const to = new Date(end);
    if (!Number.isFinite(from.getTime()) || !Number.isFinite(to.getTime()) || to <= from) {
      setError("Choose an end time after the start time.");
      return;
    }
    setSaving(true);
    try {
      const created = await createCalendarEvent({
        kind, title: title.trim(), start_at: from.toISOString(), end_at: to.toISOString(),
        location: location.trim() || null, notes: notes.trim() || null, lead_id: leadId || null,
      });
      setEvents((current) => [...current, created].sort((a, b) => a.start_at.localeCompare(b.start_at)));
      setSelectedDay(eventDay(created));
      setEditing(false);
      setNotice("Event added to the workspace calendar. No invitation email was sent.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The event could not be saved.");
    } finally {
      setSaving(false);
    }
  }

  async function remove(event: WorkspaceCalendarEvent) {
    if (!event.editable) return;
    if (!(await confirmAction({ title: `Delete “${event.title}”?`, message: "The event is removed from the workspace calendar.", confirmLabel: "Delete event", tone: "danger" }))) return;
    setError("");
    try {
      await deleteCalendarEvent(event.id);
      setEvents((current) => current.filter((row) => row.id !== event.id));
      setNotice("Manual event deleted.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The event could not be deleted.");
    }
  }

  return (
    <div className="cw-page">
      <header className="cw-header">
        <div>
          <span className="cw-eyebrow">Workspace schedule</span>
          <h1>Calendar</h1>
          <p>Meetings booked through Corral and events added by your team, together in one place.</p>
        </div>
        <div className="cw-header-actions">
          <button className="cw-button cw-button-quiet" type="button" onClick={onOpenIntegrations}>Calendar connections</button>
          {canEdit && <button className="cw-button cw-button-primary" type="button" onClick={openNewEvent}><Icon name="plus" /> New event</button>}
        </div>
      </header>

      {error && <p className="cw-alert" role="alert">{error}</p>}
      {notice && <p className="cw-notice" role="status">{notice}</p>}

      <div className="cw-layout">
        <section className="cw-calendar" aria-label="Monthly calendar">
          <div className="cw-toolbar">
            <div className="cw-month-nav">
              <button type="button" aria-label="Previous month" onClick={() => moveMonth(-1)}><Icon name="chevronLeft" /></button>
              <h2>{new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric" }).format(month)}</h2>
              <button type="button" aria-label="Next month" onClick={() => moveMonth(1)}><Icon name="chevronRight" /></button>
              <button className="cw-today" type="button" onClick={() => { const now = new Date(); setMonth(new Date(now.getFullYear(), now.getMonth(), 1)); setSelectedDay(dayKey(now)); }}>Today</button>
            </div>
            <button className="cw-refresh" type="button" onClick={() => setRevision((current) => current + 1)} disabled={loading}><Icon name="refresh" /> {loading ? "Loading…" : "Refresh"}</button>
          </div>
          <div className="cw-filters" aria-label="Event type">
            {(["all", "meeting", "call", "event"] as const).map((value) => (
              <button key={value} type="button" aria-pressed={filter === value} onClick={() => setFilter(value)}>{value === "all" ? "All" : value === "meeting" ? "Meetings" : value === "call" ? "Calls" : "Events"}</button>
            ))}
          </div>
          <div className="cw-weekdays" aria-hidden="true">{["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((name) => <span key={name}>{name}</span>)}</div>
          <div className="cw-grid">
            {days.map((day) => {
              const key = dayKey(day);
              const dayEvents = byDay.get(key) ?? [];
              return <button key={key} type="button" className="cw-day" data-outside={day.getMonth() !== month.getMonth()} data-selected={selectedDay === key} data-today={today === key} onClick={() => setSelectedDay(key)} aria-label={`${new Intl.DateTimeFormat(undefined, { dateStyle: "full" }).format(day)}, ${dayEvents.length} events`}>
                <span className="cw-day-number">{day.getDate()}</span>
                <span className="cw-day-events">{dayEvents.slice(0, 2).map((item) => <span className="cw-day-event" data-kind={item.kind} key={item.id}>{eventTime(item)} {item.title}</span>)}{dayEvents.length > 2 && <span className="cw-more">+{dayEvents.length - 2} more</span>}</span>
                {dayEvents.length > 0 && <span className="cw-day-count" aria-hidden="true">{dayEvents.length}</span>}
              </button>;
            })}
          </div>
        </section>

        <aside className="cw-agenda" aria-label="Selected day agenda">
          <div className="cw-agenda-heading">
            <span className="cw-eyebrow">Selected day</span>
            <h2>{new Intl.DateTimeFormat(undefined, { weekday: "long", month: "long", day: "numeric" }).format(new Date(`${selectedDay}T12:00:00`))}</h2>
            <p>{agenda.length} {agenda.length === 1 ? "item" : "items"} scheduled</p>
          </div>
          {agenda.length === 0 && <div className="cw-empty"><Icon name="calendar" /><strong>Nothing scheduled</strong><p>Choose another day or add an event.</p></div>}
          <div className="cw-agenda-list">
            {agenda.map((item) => <article className="cw-event" key={item.id} data-kind={item.kind}>
              <span className="cw-event-time">{eventTime(item)} · {new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date(item.end_at))}</span>
              <h3>{item.title}</h3>
              <span className="cw-source">{sourceName(item.source)} · {item.kind}</span>
              {(item.lead_name || item.company_name) && <p>{[item.lead_name, item.company_name].filter(Boolean).join(" · ")}</p>}
              {item.location && <p>{item.location}</p>}
              {item.notes && <p className="cw-event-notes">{item.notes}</p>}
              <div className="cw-event-actions">
                {item.lead_id && <button type="button" onClick={() => onOpenLead(item.lead_id!)}>Open lead</button>}
                {item.event_url && <a href={item.event_url} target="_blank" rel="noopener noreferrer">Open booking</a>}
                {item.editable && canEdit && <button type="button" className="cw-delete" onClick={() => void remove(item)}>Delete</button>}
              </div>
            </article>)}
          </div>
        </aside>
      </div>

      {editing && <div className="cw-backdrop" role="presentation" onClick={() => !saving && setEditing(false)}>
        <section className="cw-dialog" role="dialog" aria-modal="true" aria-labelledby="cw-dialog-title" onClick={(event) => event.stopPropagation()}>
          <div className="cw-dialog-heading"><div><span className="cw-eyebrow">Manual entry</span><h2 id="cw-dialog-title">New calendar event</h2></div><button type="button" aria-label="Close" onClick={() => setEditing(false)} disabled={saving}><Icon name="close" /></button></div>
          <form onSubmit={(event) => void save(event)}>
            <label>Type<select value={kind} onChange={(event) => setKind(event.target.value as WorkspaceCalendarEvent["kind"])}><option value="meeting">Meeting</option><option value="call">Call</option><option value="event">Event</option></select></label>
            <label>Title<input value={title} onChange={(event) => setTitle(event.target.value)} required maxLength={255} placeholder="e.g. Discovery call" /></label>
            <div className="cw-form-row"><label>Starts<input type="datetime-local" value={start} onChange={(event) => setStart(event.target.value)} required /></label><label>Ends<input type="datetime-local" value={end} onChange={(event) => setEnd(event.target.value)} required /></label></div>
            <label>Related lead <span>(optional)</span><select value={leadId} onChange={(event) => setLeadId(event.target.value)}><option value="">No lead</option>{leads.map((lead) => <option key={lead.id} value={lead.id}>{lead.full_name || lead.company_name} · {lead.company_name}</option>)}</select></label>
            <label>Location or link <span>(optional)</span><input value={location} onChange={(event) => setLocation(event.target.value)} maxLength={500} placeholder="Office, phone, or meeting link" /></label>
            <label>Notes <span>(optional)</span><textarea value={notes} onChange={(event) => setNotes(event.target.value)} maxLength={4000} rows={3} /></label>
            <p className="cw-form-hint">This records an event in Corral. It does not send an invitation or create an event in Google Calendar or Calendly.</p>
            <div className="cw-form-actions"><button className="cw-button cw-button-quiet" type="button" onClick={() => setEditing(false)} disabled={saving}>Cancel</button><button className="cw-button cw-button-primary" type="submit" disabled={saving}>{saving ? "Saving…" : "Save event"}</button></div>
          </form>
        </section>
      </div>}
    </div>
  );
}
