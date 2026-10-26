import { useEffect, useState, type FormEvent } from "react";
import { getLead, getLeadClosingOptions, updateLeadProgress } from "../../api";
import type { Lead } from "../../types";
import { Icon } from "../ui/Icon";
import { Banner, Button, Segmented, Sheet, titleize } from "../ui/primitives";
import {
  dealActions,
  flushDealChanges,
  formatMoney,
  stageInfo,
  stageIndex,
  type DealStage,
  type DealView,
  type ProgressStage,
} from "./dealStore";

const SUGGESTIONS: Partial<Record<DealStage, string[]>> = {
  contacted: ["Called and introduced our team.", "Sent a first email.", "Left a voicemail and followed up by email."],
  engaged: ["They replied and want to talk.", "Discovery call booked.", "Meeting held; next steps agreed."],
  quoted: ["Quotation sent by email.", "Proposal walked through on a call."],
  negotiation: ["Client asked for a lower price.", "Scope being trimmed to fit budget.", "Payment terms under discussion."],
  contract: ["Price and scope agreed; contract sent.", "SLA terms shared for review."],
  won: ["Contract signed.", "Purchase order received.", "Deposit paid."],
  lost: ["Chose another vendor.", "Budget not approved.", "No response after several follow-ups.", "Timing — revisit next quarter."],
  new: ["Moved back to start over."],
};

/**
 * Confirms a stage move. Moves the API can record (contacted, engaged, quoted,
 * won, lost) go through the same progress endpoint as before — with a written
 * comment, and Corral re-checks the lead afterwards. Negotiation and contract
 * are kept on this device until the API records them.
 */
export function StageMoveSheet({
  view,
  target,
  onClose,
  onMoved,
}: {
  view: DealView;
  target: DealStage;
  onClose: () => void;
  onMoved: (lead: Lead | null, message: string) => void;
}) {
  const info = stageInfo(target);
  const from = stageInfo(view.stage);
  const lead = view.lead;
  const closed = view.stageSource === "outcome";
  const backward = stageIndex(target) < stageIndex(view.stage) && target !== "lost";
  const backendStages = info.backend;
  const [backendStage, setBackendStage] = useState<ProgressStage | null>(backendStages?.[0] ?? null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [value, setValue] = useState(view.value != null ? view.value.toFixed(2) : "");
  const [conversion, setConversion] = useState<"project" | "sale">("project");
  const [members, setMembers] = useState<Array<{ user_id: string; display_name: string; slack_linked: boolean }>>([]);
  const [credited, setCredited] = useState(lead.outcome?.credited_user_id ?? lead.routing?.assigned_rep_id ?? "");
  const [directMessage, setDirectMessage] = useState(false);
  const closing = target === "won" || target === "lost";

  useEffect(() => {
    if (!closing) return;
    const controller = new AbortController();
    getLeadClosingOptions(controller.signal)
      .then((rows) => {
        if (controller.signal.aborted) return;
        setMembers(rows);
        setCredited((current) => (rows.some((member) => member.user_id === current) ? current : ""));
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [closing]);

  // A closed lead can only have its outcome corrected (won ↔ lost); the API refuses reopening.
  const blocked = closed && !closing
    ? "This lead is closed. Corral can correct a recorded outcome (Won or Lost) but can’t reopen it."
    : null;
  // The API only moves forward through progress stages; earlier stages are kept on this device.
  const recordsInApi = Boolean(backendStage) && !backward;
  const needsNote = recordsInApi;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (blocked) return;
    setBusy(true);
    setError(null);
    try {
      if (recordsInApi && backendStage) {
        await updateLeadProgress(lead.id, {
          stage: backendStage,
          note: note.trim(),
          ...(target === "won"
            ? { conversion_type: conversion, closed_revenue: value === "" ? null : Number(value), send_direct_message: directMessage }
            : {}),
          ...(closing ? { credited_user_id: credited || null } : {}),
        });
        // Recorded stages come from the API; a leftover device stage would hide them.
        dealActions.setStage(lead.id, closing ? null : target, note.trim());
        await flushDealChanges();
        let fresh: Lead | null = null;
        try { fresh = await getLead(lead.id); } catch { fresh = null; }
        onMoved(
          fresh,
          target === "won"
            ? `${lead.company_name || lead.full_name} recorded as won${value ? ` · ${formatMoney(Number(value))}` : ""}.`
            : target === "lost"
              ? `${lead.company_name || lead.full_name} recorded as lost.`
              : `${lead.company_name || lead.full_name} moved to ${info.label}. Corral re-checked the lead.`,
        );
      } else {
        dealActions.setStage(lead.id, target, note.trim() || undefined);
        await flushDealChanges();
        onMoved(null, `${lead.company_name || lead.full_name} moved to ${info.label}.`);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The move couldn’t be saved.");
    } finally {
      setBusy(false);
    }
  }

  const disabled = busy || Boolean(blocked) || (needsNote && note.trim().length < 3);
  return (
    <Sheet
      title={target === "won" ? "Record a win" : target === "lost" ? "Record as lost" : `Move to ${info.label}`}
      subtitle={lead.company_name || lead.full_name}
      onClose={onClose}
      width={520}
      dismissible={!busy}
    >
      <form className="d-move" onSubmit={(event) => void submit(event)}>
        <div className="d-move-path" aria-label={`From ${from.label} to ${info.label}`}>
          <span data-stage={view.stage}>{from.label}</span>
          <Icon name="arrowRight" size={16} />
          <span data-stage={target} data-current>{info.label}</span>
        </div>
        <p className="d-move-hint">{info.hint}</p>

        {blocked && <Banner tone="attention" title="Can’t move this lead">{blocked}</Banner>}

        {!blocked && backendStages && backendStages.length > 1 && !backward && (
          <Segmented
            label="What happened"
            value={backendStage ?? backendStages[0]}
            onChange={(next) => setBackendStage(next)}
            options={backendStages.map((stage) => ({ value: stage, label: titleize(stage) }))}
          />
        )}

        {!blocked && target === "won" && (
          <div className="d-move-grid">
            <label className="v-field">
              <span>Deal value (USD)</span>
              <input type="number" inputMode="decimal" min="0" step="0.01" value={value} onChange={(event) => setValue(event.target.value)} placeholder="Leave blank if unknown" />
            </label>
            <label className="v-field">
              <span>Won as</span>
              <select value={conversion} onChange={(event) => setConversion(event.target.value as "project" | "sale")}>
                <option value="project">Project</option>
                <option value="sale">Sale</option>
              </select>
            </label>
          </div>
        )}
        {!blocked && closing && (
          <label className="v-field">
            <span>Credit to</span>
            <select value={credited} onChange={(event) => setCredited(event.target.value)}>
              <option value="">Assigned member, if linked</option>
              {members.map((member) => <option key={member.user_id} value={member.user_id}>{member.display_name}</option>)}
            </select>
          </label>
        )}
        {!blocked && target === "won" && (
          <label className="v-toggle-row">
            <span>
              <b>Send a private Slack congratulations</b>
              <small>Off by default. Needs the member’s linked Slack ID.</small>
            </span>
            <input type="checkbox" role="switch" className="v-switch" checked={directMessage} disabled={!credited} onChange={(event) => setDirectMessage(event.target.checked)} />
          </label>
        )}

        {!blocked && (
          <label className="v-field">
            <span>{needsNote ? "What happened (required)" : "Note (optional)"}</span>
            <textarea rows={3} minLength={needsNote ? 3 : undefined} maxLength={2000} value={note} onChange={(event) => setNote(event.target.value)} placeholder="A short, verified comment for the record" />
          </label>
        )}
        {!blocked && (SUGGESTIONS[target]?.length ?? 0) > 0 && (
          <div className="d-chips" role="group" aria-label="Suggested comments">
            {SUGGESTIONS[target]!.map((text) => (
              <button key={text} type="button" className="d-chip" onClick={() => setNote(text)}>{text}</button>
            ))}
          </div>
        )}

        {!blocked && (
          <p className="d-move-foot">
            <Icon name={recordsInApi ? "shield" : "info"} size={14} />
            {recordsInApi
              ? target === "won" || target === "lost"
                ? "Recorded in Corral. Qualification is unchanged and no CRM record is created."
                : "Recorded in Corral’s history. Corral re-checks the lead afterwards."
              : backward
                ? "The current stage is saved to the workspace; previous activity remains in history."
                : "Saved to the workspace with its stage history."}
          </p>
        )}
        {error && <Banner tone="danger" title="Not saved">{error}</Banner>}

        <div className="d-move-actions">
          <Button onClick={onClose} disabled={busy}>Cancel</Button>
          <Button kind={target === "lost" ? "destructive" : "filled"} type="submit" disabled={disabled}>
            {busy ? "Saving…" : target === "won" ? "Record Win" : target === "lost" ? "Record as Lost" : `Move to ${info.label}`}
          </Button>
        </div>
      </form>
    </Sheet>
  );
}
