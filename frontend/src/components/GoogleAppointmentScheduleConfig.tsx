import { useEffect, useState } from "react";
import { updateGoogleAppointmentSchedule } from "../api";
import type { ProviderConnection } from "../types";

type Props = {
  connection: ProviderConnection;
  canManage: boolean;
  onSaved: (message: string) => void;
};

const metadataText = (connection: ProviderConnection, key: string) => {
  const value = connection.metadata[key];
  return typeof value === "string" ? value : "";
};

export function GoogleAppointmentScheduleConfig({ connection, canManage, onSaved }: Props) {
  const [url, setUrl] = useState(() => metadataText(connection, "google_appointment_schedule_url"));
  const [eventTitle, setEventTitle] = useState(() => metadataText(connection, "google_appointment_schedule_title"));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setUrl(metadataText(connection, "google_appointment_schedule_url"));
    setEventTitle(metadataText(connection, "google_appointment_schedule_title"));
  }, [connection.id, connection.updated_at]);

  const save = async () => {
    setBusy(true);
    setError("");
    try {
      const updated = await updateGoogleAppointmentSchedule(connection.id, url.trim(), eventTitle.trim());
      setUrl(metadataText(updated, "google_appointment_schedule_url"));
      setEventTitle(metadataText(updated, "google_appointment_schedule_title"));
      onSaved(url.trim() ? "Google appointment schedule saved." : "Google appointment schedule removed.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The appointment schedule could not be saved.");
    } finally {
      setBusy(false);
    }
  };

  return <section className="google-schedule-config" aria-label="Google appointment schedule">
    <style>{`
      .google-schedule-config{display:grid;gap:10px;margin:14px 0;padding:14px;border:1px solid var(--line);border-radius:10px;background:var(--surface-high)}
      .google-schedule-config h3{margin:0;font-size:14px}
      .google-schedule-config p,.google-schedule-config small{margin:0;color:var(--muted);font-size:13px;line-height:1.5}
      .google-schedule-config label{display:grid;gap:5px;color:var(--text);font-size:13px;font-weight:600}
      .google-schedule-config input{width:100%;min-height:40px;padding:8px 10px;border:1px solid var(--line);border-radius:7px;background:var(--surface);color:var(--text);font:inherit}
      .google-schedule-config .gsc-actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
      .google-schedule-config .gsc-error{color:var(--red)}
    `}</style>
    <div>
      <h3>Google appointment schedule</h3>
      <p>Paste the public Google appointment schedule link. Set the event title to the exact title Google puts on booked calendar events so Corral can match bookings to the lead.</p>
    </div>
    <label>Appointment schedule link
      <input type="url" value={url} disabled={!canManage || busy} placeholder="https://calendar.app.google/…" onChange={(event) => setUrl(event.target.value)} />
    </label>
    <label>Exact calendar event title
      <input value={eventTitle} disabled={!canManage || busy} maxLength={255} placeholder="Introductory call" onChange={(event) => setEventTitle(event.target.value)} />
    </label>
    <div className="gsc-actions">
      {canManage && <button type="button" onClick={() => void save()} disabled={busy || ((!url.trim()) !== (!eventTitle.trim()))}>
        {busy ? "Saving…" : url.trim() ? "Save schedule link" : "Remove schedule link"}
      </button>}
      {error && <small className="gsc-error" role="alert">{error}</small>}
    </div>
  </section>;
}
