import { useEffect, useState } from "react";

import {
  deleteCorralAccount,
  getAccountDeletionPreview,
  type AccountDeletionPreview,
} from "../api";
import { Button } from "../v3/ui/primitives";
import { confirmAction } from "../v3/ui/confirm";

function workspaceDataSummary(workspace: AccountDeletionPreview["owned_workspaces"][number]) {
  return [
    [workspace.leads, "lead"],
    [workspace.integrations, "integration"],
    [workspace.stored_secrets, "stored secret"],
    [workspace.source_events, "source event"],
    [workspace.drafts, "email draft"],
    [workspace.conversations, "conversation"],
    [workspace.messages, "message"],
  ]
    .filter(([count]) => Number(count) > 0)
    .map(([count, label]) => `${count} ${label}${Number(count) === 1 ? "" : "s"}`)
    .join(", ");
}

export function AccountDeletionPanel() {
  const [preview, setPreview] = useState<AccountDeletionPreview | null>(null);
  const [typedEmail, setTypedEmail] = useState("");
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    getAccountDeletionPreview()
      .then((result) => {
        if (active) setPreview(result);
      })
      .catch((loadError) => {
        if (active) setError(loadError instanceof Error ? loadError.message : "Unable to load account deletion details.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const emailMatches = Boolean(
    preview && typedEmail.trim().toLowerCase() === preview.email.toLowerCase(),
  );

  const removeAccount = async () => {
    if (!preview || !emailMatches || !preview.can_delete || deleting) return;
    const hasOwnedWorkspaces = preview.owned_workspaces.length > 0;
    const confirmed = await confirmAction({
      title: "Permanently delete your Corral account?",
      message: hasOwnedWorkspaces
        ? "This also permanently deletes the workspace and its data listed below. Your shared workspaces will remain, but your access will be removed."
        : "Your account and sign-in records will be permanently removed. Your shared workspaces will remain, but your access will be removed.",
      confirmLabel: "Delete account",
      tone: "danger",
    });
    if (!confirmed) return;

    setDeleting(true);
    setError(null);
    try {
      await deleteCorralAccount(preview.email);
      window.location.assign("/");
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Your account could not be deleted.");
      setDeleting(false);
    }
  };

  return (
    <section className="st-card account-deletion-panel" aria-labelledby="account-delete-title">
      <div className="account-deletion-heading">
        <div>
          <h2 id="account-delete-title">Delete Corral account</h2>
          <p className="st-card-sub">
            Permanently remove your own account. Workspace admins cannot delete someone else’s Corral account here.
          </p>
        </div>
      </div>

      {loading ? (
        <p className="account-deletion-muted">Checking your workspaces…</p>
      ) : error && !preview ? (
        <p className="account-deletion-error" role="alert">{error}</p>
      ) : preview ? (
        <>
          <div className="account-deletion-warning" role="note">
            <strong>This cannot be undone.</strong>
            <span>
              Any workspace you own is permanently deleted with all its data and connected accounts.
              In workspaces you do not own, only your membership is removed.
            </span>
          </div>

          {preview.owned_workspaces.length > 0 && (
            <div className="account-deletion-owned">
              <h3>Workspaces that will be deleted</h3>
              <ul>
                {preview.owned_workspaces.map((workspace) => (
                  <li key={workspace.id}>
                    <strong>{workspace.name}</strong>
                    <span>{workspaceDataSummary(workspace) || "No stored records found"}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {preview.shared_workspaces > 0 && (
            <p className="account-deletion-muted">
              Your membership will be removed from {preview.shared_workspaces} other workspace{preview.shared_workspaces === 1 ? "" : "s"}; those workspaces and their data stay.
            </p>
          )}
          {preview.invitations_to_cancel > 0 && (
            <p className="account-deletion-muted">
              {preview.invitations_to_cancel} invitation{preview.invitations_to_cancel === 1 ? "" : "s"} sent from your account will be canceled.
            </p>
          )}

          {preview.blockers.length > 0 && (
            <div className="account-deletion-error" role="alert">
              {preview.blockers.map((blocker) => <p key={blocker}>{blocker}</p>)}
            </div>
          )}

          {preview.recent_sign_in_required && (
            <p className="account-deletion-muted" role="status">
              For your security, sign out and sign back in, then return here before deleting your account.
            </p>
          )}

          {preview.blockers.length === 0 && (
            <div className="account-deletion-confirm">
              <label htmlFor="account-delete-email">
                Type <strong>{preview.email}</strong> to confirm
                <input
                  id="account-delete-email"
                  type="email"
                  autoComplete="email"
                  value={typedEmail}
                  onChange={(event) => setTypedEmail(event.currentTarget.value)}
                  disabled={deleting}
                />
              </label>
              {error && <p className="account-deletion-error" role="alert">{error}</p>}
              <Button
                kind="destructive"
                onClick={() => void removeAccount()}
                disabled={!preview.can_delete || !emailMatches || deleting}
              >
                {deleting ? "Deleting account…" : "Delete my Corral account"}
              </Button>
            </div>
          )}
        </>
      ) : null}
    </section>
  );
}
