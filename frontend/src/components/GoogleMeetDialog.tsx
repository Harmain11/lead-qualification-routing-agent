import { useEffect, useRef, useState, type FormEvent } from "react";
import { createPortal } from "react-dom";
import { createGoogleMeetEvent, listGoogleMeetConnections } from "../api";

type Result = { event_id: string; event_url: string | null; meet_url: string | null };

export function GoogleMeetDialog({ onClose, onCreated, defaultAttendees = "", defaultTitle = "" }: {
  onClose: () => void; onCreated: (result: Result) => void; defaultAttendees?: string; defaultTitle?: string;
}) {
  const [connections, setConnections] = useState<Array<{ id: string; name: string }>>([]);
  const [connection, setConnection] = useState("");
  const [title, setTitle] = useState(defaultTitle);
  const [description, setDescription] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [attendees, setAttendees] = useState(defaultAttendees);
  const [reviewing, setReviewing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestId = useRef(crypto.randomUUID());
  const busyRef = useRef(false);
  const dialogRef = useRef<HTMLFormElement>(null);
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

  useEffect(() => {
    let active = true;
    void listGoogleMeetConnections().then((items) => {
      if (active) { setConnections(items); setConnection(items[0]?.id || ""); }
    }).catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "Could not load calendars."); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const dialog = dialogRef.current;
    dialog?.querySelector<HTMLElement>("button, select, input")?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busyRef.current) { event.preventDefault(); onClose(); return; }
      if (event.key !== "Tab" || !dialog) return;
      const controls = Array.from(dialog.querySelectorAll<HTMLElement>("button:not(:disabled), select:not(:disabled), input:not(:disabled), textarea:not(:disabled)"));
      if (!controls.length) { event.preventDefault(); return; }
      if (event.shiftKey && document.activeElement === controls[0]) { event.preventDefault(); controls[controls.length - 1].focus(); }
      else if (!event.shiftKey && document.activeElement === controls[controls.length - 1]) { event.preventDefault(); controls[0].focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); previous?.focus(); };
  }, []);

  function review(event: FormEvent) {
    event.preventDefault();
    event.stopPropagation();
    if (!connection || !title.trim() || !start || !end || new Date(end) <= new Date(start)) {
      setError("Choose a calendar, title, and valid start and end times."); return;
    }
    setError(null); setReviewing(true);
  }

  async function create() {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true); setAttempted(true); setError(null);
    try {
      const result = await createGoogleMeetEvent({
        request_id: requestId.current, connection_id: connection, title: title.trim(), description,
        start_at: new Date(start).toISOString(), end_at: new Date(end).toISOString(),
        time_zone: zone, attendees: attendees.split(/[,;\n]/).map((item) => item.trim()).filter(Boolean),
      });
      onCreated(result);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not create the event."); }
    finally { busyRef.current = false; setBusy(false); }
  }

  return createPortal(<div className="team-modal-backdrop"><form ref={dialogRef} className="team-modal panel" role="dialog" aria-modal="true" aria-label="Create Google Meet event" onSubmit={review}>
    <div className="team-modal-head"><div><span className="eyebrow">Google Calendar</span><h2>{reviewing ? "Review meeting" : "Create Google Meet"}</h2><p>{reviewing ? "Creating this event sends invitations to the listed attendees." : "Set up a meeting and review the details before sending invitations."}</p></div><button type="button" className="modal-close" aria-label="Close" disabled={busy} onClick={onClose}>×</button></div>
    {error && <div className="api-error" role="alert">{error}</div>}
    {error && attempted && <p>Retrying uses the same event ID. If you need to change the details, first check Google Calendar for an event that may already have been created.</p>}
    {reviewing ? <div><p><strong>{title}</strong></p><p>{new Date(start).toLocaleString()} – {new Date(end).toLocaleString()} · {zone}</p><p>Calendar: {connections.find((item) => item.id === connection)?.name}</p><p>Attendees: {attendees || "None"}</p>{description && <p>{description}</p>}</div> : <>
      <label className="team-field"><span>Calendar</span><select value={connection} required onChange={(event) => setConnection(event.target.value)}><option value="">Select a connected calendar</option>{connections.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      {!connections.length && <p>Connect an authorized Google Calendar in Integrations to create a meeting.</p>}
      <label className="team-field"><span>Title</span><input value={title} maxLength={255} required onChange={(event) => setTitle(event.target.value)} /></label>
      <label className="team-field"><span>Start ({zone})</span><input type="datetime-local" required value={start} onChange={(event) => setStart(event.target.value)} /></label>
      <label className="team-field"><span>End ({zone})</span><input type="datetime-local" required value={end} onChange={(event) => setEnd(event.target.value)} /></label>
      <label className="team-field"><span>Attendee emails, separated by commas</span><input value={attendees} onChange={(event) => setAttendees(event.target.value)} /></label>
      <label className="team-field"><span>Description (optional)</span><textarea maxLength={4000} value={description} onChange={(event) => setDescription(event.target.value)} /></label>
    </>}
    <div className="team-modal-actions">{reviewing ? <><button type="button" className="button-quiet" disabled={busy || attempted} onClick={() => setReviewing(false)}>Edit details</button><button type="button" className="primary-button" disabled={busy} onClick={() => void create()}>{busy ? "Creating…" : attempted ? "Retry event creation" : "Create event and invite"}</button></> : <><button type="button" className="button-quiet" onClick={onClose}>Cancel</button><button className="primary-button" disabled={!connection}>Review meeting</button></>}</div>
  </form></div>, document.querySelector(".app-shell") || document.body);
}
