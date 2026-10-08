import { useEffect, useRef, useState } from "react";
import type { AuthSession } from "../types";
import "./workspaceSwitcher.css";

export function WorkspaceSwitcher({
  session,
  onSelect,
}: {
  session: AuthSession;
  onSelect: (workspaceId: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const current = session.workspaces.find((workspace) => workspace.id === session.current_workspace_id);

  useEffect(() => {
    if (!open) return;
    const element = dialog.current;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    element?.showModal();
    return () => {
      element?.close();
      document.body.style.overflow = previousOverflow;
    };
  }, [open]);

  async function select(workspaceId: string) {
    if (busy) return;
    if (workspaceId === session.current_workspace_id) {
      setOpen(false);
      return;
    }
    setBusy(workspaceId);
    setError(null);
    try {
      await onSelect(workspaceId);
      setOpen(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The workspace could not be opened.");
    } finally {
      setBusy(null);
    }
  }

  return <div className="workspace-switcher">
    <button
      type="button"
      className="workspace-trigger"
      aria-haspopup="dialog"
      aria-label={`Workspaces: ${current?.name || "Workspace name unavailable"}`}
      onClick={() => { setError(null); setOpen(true); }}
    >
      <span className="workspace-trigger-label">Workspaces</span>
      <strong>{current?.name || "Workspace name unavailable"}</strong>
      <span className="workspace-trigger-action">Choose workspace</span>
    </button>
    <small>{current?.is_demo ? "Illustrative workspace" : "Live workspace"}</small>
    {open && <dialog
      ref={dialog}
      className="workspace-chooser"
      aria-labelledby="workspace-chooser-title"
      onCancel={(event) => { if (busy) event.preventDefault(); else setOpen(false); }}
    >
      <div className="workspace-chooser-inner">
        <header className="workspace-chooser-heading">
          <div><span className="eyebrow">Your workspace access</span><h1 id="workspace-chooser-title">Workspaces</h1><p>Select a workspace. Connections, leads and permissions stay separate.</p></div>
          <button type="button" className="button-quiet" onClick={() => setOpen(false)} disabled={Boolean(busy)} autoFocus>Back to Corral</button>
        </header>
        {error && <div className="notice" role="alert">{error}</div>}
        <div className="workspace-options">
          {session.workspaces.map((workspace) => {
            const selected = workspace.id === session.current_workspace_id;
            return <button
              type="button"
              className={`workspace-option${selected ? " selected" : ""}`}
              key={workspace.id}
              aria-current={selected ? "true" : undefined}
              disabled={Boolean(busy)}
              onClick={() => void select(workspace.id)}
            >
              <span><strong>{workspace.name || "Workspace name unavailable"}</strong><small>{workspace.role.replaceAll("_", " ")} · {workspace.is_demo ? "Illustrative data" : "Live data"}</small></span>
              <span className="workspace-option-status">{busy === workspace.id ? "Opening…" : selected ? "Current workspace" : "Open workspace"}</span>
            </button>;
          })}
        </div>
      </div>
    </dialog>}
  </div>;
}
