import { useEffect, useState } from "react";
import { createClientId } from "../clientIds";
import {
  opsGet,
  opsSave,
  type Collaboration,
  type Note,
} from "../operationsApi";
import { getLead, reevaluateLead } from "../api";
import type { Lead } from "../types";
export function CollaborationPanel({
  leadId,
  canEdit,
  onLeadUpdated,
  refreshKey = 0,
}: {
  leadId: string;
  canEdit: boolean;
  onLeadUpdated?: (lead: Lead) => void;
  refreshKey?: number;
}) {
  const [data, setData] = useState<Collaboration>({ notes: [], presence: [] });
  const [draft, setDraft] = useState<{
    id: string;
    body: string;
    version: number;
  } | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    const load = () =>
      opsGet<Collaboration>(
        `/v1/collaboration/leads/${leadId}`,
        controller.signal,
      )
        .then((value) => {
          if (active) setData(value);
        })
        .catch((reason) => {
          if (active && !controller.signal.aborted)
            setError(String(reason.message));
        });
    const heartbeat = () =>
      opsSave(`/v1/collaboration/leads/${leadId}/presence`, {}, "POST", {
        activityToast: false,
      }).catch(() => {});
    void load();
    void heartbeat();
    const timer = setInterval(() => {
      void load();
      void heartbeat();
    }, 15000);
    return () => {
      active = false;
      controller.abort();
      clearInterval(timer);
    };
  }, [leadId, refreshKey]);
  const edit = (note: Note) => {
    setDraft({ id: note.id, body: note.body, version: note.version });
    setError("");
  };
  async function save() {
    if (!draft) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await opsSave(
        `/v1/collaboration/leads/${leadId}/notes/${draft.id}`,
        { body: draft.body, version: draft.version },
        "PUT",
      );
      setData(await opsGet<Collaboration>(`/v1/collaboration/leads/${leadId}`));
      setDraft(null);
      try {
        onLeadUpdated?.(await reevaluateLead(leadId));
        setNotice("Note saved and included in the updated AI assessment.");
      } catch {
        try {
          onLeadUpdated?.(await getLead(leadId));
        } catch {
          // The note remains saved even if lead details cannot be refreshed.
        }
        setNotice("Note saved, but the AI assessment could not be refreshed. Try re-evaluating again later.");
      }
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Unable to save note",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="ops-panel">
      <div className="ops-toolbar">
        <div>
          <h3>Team notes</h3>
          <p aria-live="polite">
            {data.presence.length
              ? data.presence.map((person) => person.name).join(", ") +
                " viewing this lead"
              : "No other viewing presence recorded"}
          </p>
        </div>
        {canEdit && (
          <button
            className="secondary-button"
            onClick={() =>
              setDraft({ id: createClientId(), body: "", version: 0 })
            }
          >
            Add note
          </button>
        )}
      </div>
      {error && (
        <p role="alert" className="team-alert">
          {error}
        </p>
      )}
      {notice && <p className="team-notice" role="status">{notice}</p>}
      {data.notes.map((note) => (
        <article className="ops-note" key={note.id}>
          <p style={{ whiteSpace: "pre-wrap" }}>{note.body}</p>
          <small>Updated {new Date(note.updated_at).toLocaleString()}</small>
          {canEdit && (
            <button className="button-quiet" onClick={() => edit(note)}>
              Edit
            </button>
          )}
        </article>
      ))}
      {!data.notes.length && !draft && (
        <p className="ops-muted">
          Share a question, observation or handoff here. Teammates see saved
          changes while viewing the lead.
        </p>
      )}
      {draft && (
        <div className="ops-form">
          <label>
            Shared note
            <textarea
              rows={4}
              value={draft.body}
              maxLength={5000}
              onChange={(event) =>
                setDraft({ ...draft, body: event.target.value })
              }
            />
          </label>
          {error && (
            <button
              className="button-quiet"
              onClick={() => {
                const saved = data.notes.find((note) => note.id === draft.id);
                if (saved) {
                  setDraft({ ...draft, version: saved.version });
                  setError("");
                }
              }}
            >
              Use latest saved version for my merged text
            </button>
          )}
          <div className="ops-toolbar">
            <button className="button-quiet" onClick={() => setDraft(null)}>
              Cancel
            </button>
            <button
              className="primary-button"
              disabled={busy || !draft.body.trim()}
              onClick={() => void save()}
            >
              {busy ? "Saving…" : "Save note"}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
