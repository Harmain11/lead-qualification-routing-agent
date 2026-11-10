import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { OperationsDialog } from "./OperationsDialog";
import {
  addIntegrationCredential,
  backfillReasoning,
  getReasoningBackfillStatus,
  reconnectIntegration,
  testIntegration,
  updateReasoningModel,
} from "../api";
import type { ReasoningBackfillStatus } from "../api";
import type { IntegrationCard } from "../types";

function withTimeout<T>(promise: Promise<T>, milliseconds = 45000): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      reject(new Error("Provider verification timed out. Check the key and backend connection, then try again."));
    }, milliseconds);
    promise.then(resolve, reject).finally(() => window.clearTimeout(timer));
  });
}

export function OpenAISetupPrompt({
  card,
  cards,
  onVerified,
  onClose,
  onLater,
  onSettings,
}: {
  card: IntegrationCard;
  cards: IntegrationCard[];
  onVerified: () => void;
  onClose: () => void;
  onLater: () => void;
  onSettings: () => void;
}) {
  const availableCards = cards.length > 0 ? cards : [card];
  const [provider, setProvider] = useState(card.provider);
  const selectedCard = availableCards.find((item) => item.provider === provider) ?? card;
  const [apiKey, setApiKey] = useState("");
  const [accountName, setAccountName] = useState("");
  const [model, setModel] = useState(selectedCard.connection?.selected_model ?? selectedCard.default_model ?? selectedCard.supported_models[0] ?? "");
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState<"saving" | "verifying" | "backfilling" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [connectionId, setConnectionId] = useState<string | null>(
    selectedCard.connection?.id ?? null,
  );
  const [jobId, setJobId] = useState<string | null>(() => {
    const id = selectedCard.connection?.id;
    return id ? window.localStorage.getItem(`corral:reasoning-backfill:${id}`) : null;
  });
  const [jobStatus, setJobStatus] = useState<ReasoningBackfillStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const isOpenAI = selectedCard.provider === "openai";

  function chooseProvider(nextProvider: string) {
    const nextCard = availableCards.find((item) => item.provider === nextProvider) ?? card;
    setProvider(nextProvider);
    setModel(nextCard.connection?.selected_model ?? nextCard.default_model ?? nextCard.supported_models[0] ?? "");
    setApiKey("");
    setError(null);
  }

  useEffect(() => {
    setModel(selectedCard.connection?.selected_model ?? selectedCard.default_model ?? selectedCard.supported_models[0] ?? "");
  }, [selectedCard]);

  useEffect(() => {
    const id = selectedCard.connection?.id;
    if (!id) return;
    setConnectionId(id);
    const saved = window.localStorage.getItem(`corral:reasoning-backfill:${id}`);
    if (saved) setJobId(saved);
  }, [selectedCard.connection?.id]);

  useEffect(() => {
    if (!connectionId || !jobId) return;
    let active = true;
    const poll = async () => {
      try {
        const result = await getReasoningBackfillStatus(connectionId, jobId);
        if (active) {
          setJobStatus(result);
          setStatusError(null);
        }
      } catch (requestError) {
        if (active) setStatusError(requestError instanceof Error ? requestError.message : "Progress is temporarily unavailable.");
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 2000);
    return () => { active = false; window.clearInterval(timer); };
  }, [connectionId, jobId]);

  if (!card) return null;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!apiKey.trim() || (selectedCard.supported_models.length > 0 && !model)) return;
    setBusy(true);
    setPhase("saving");
    setError(null);
    try {
      const connection = selectedCard.connection
        ? await withTimeout(reconnectIntegration(selectedCard.connection.id, { api_key: apiKey.trim() }))
        : await withTimeout(addIntegrationCredential(
          selectedCard.provider,
          isOpenAI ? "OpenAI project" : accountName.trim() || `${selectedCard.display_name} account`,
          "api_key",
          selectedCard.required_scopes,
          { api_key: apiKey.trim() },
          isOpenAI ? { selected_model: model } : {},
        ));
      setApiKey("");
      if (model && connection.selected_model !== model) {
        await withTimeout(updateReasoningModel(connection.id, model));
      }
      setPhase("verifying");
      const result = await withTimeout(testIntegration(connection.id));
      if (!result.verified) throw new Error(result.message);
      setConnectionId(connection.id);
      const queued = result.backfill_job_id
        ? { job_id: result.backfill_job_id }
        : await withTimeout(backfillReasoning(connection.id), 5000);
      window.localStorage.setItem(`corral:reasoning-backfill:${connection.id}`, queued.job_id);
      setJobId(queued.job_id);
      onVerified();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : `${selectedCard.display_name} setup failed. Check the key and provider access.`);
    } finally {
      setBusy(false);
      setPhase(null);
    }
  }

  if (jobId) {
    const status = jobStatus?.status ?? "queued";
    const terminal = ["completed", "partial_failure", "failed", "no_eligible_leads"].includes(status);
    const total = jobStatus?.total ?? 0;
    const progress = total ? Math.min(100, Math.round(((jobStatus?.completed ?? 0) + (jobStatus?.failed ?? 0) + (jobStatus?.skipped ?? 0)) / total * 100)) : 0;
    const heading = status === "no_eligible_leads" ? "No leads need analysis" : status === "completed" ? "Analysis backfill complete" : status === "partial_failure" ? "Backfill completed with some failures" : status === "failed" ? "Backfill could not complete" : "AI analysis has started";
    return <OperationsDialog title="AI analysis progress" className="team-modal openai-setup-prompt reasoning-backfill-modal" showHeader={false} onClose={onClose}>
      <div className="team-modal-head"><div><span className="eyebrow">Workspace analysis</span><h2>{heading}</h2><p>{jobStatus?.provider ?? selectedCard.display_name}{jobStatus?.model ? ` · ${jobStatus.model}` : ""}</p></div><button className="button-quiet" onClick={onClose}>Close</button></div>
      <p className="backfill-live-status" role="status">{terminal ? "This background job has finished." : status === "running" ? "Analyzing eligible leads in the background…" : "The background job is queued and will continue if you close this window."}</p>
      {status !== "no_eligible_leads" && <div className="backfill-progress"><div className="backfill-progress-track"><span style={{ width: `${progress}%` }} /></div><div className="backfill-counts"><strong>{jobStatus?.completed ?? 0} / {total} completed</strong><span>{jobStatus?.failed ?? 0} failed · {jobStatus?.skipped ?? 0} skipped</span></div></div>}
      {statusError && <p className="team-alert" role="status">{statusError} The job continues; progress will retry automatically.</p>}
      {status === "failed" && jobStatus?.message && <p className="team-alert" role="alert">{jobStatus.message}</p>}
      {status === "partial_failure" && <p className="backfill-note">Successful analyses were saved. Failed leads remain unchanged and can be retried.</p>}
      {!!jobStatus?.failures?.length && <div className="backfill-note" role="status">
        <strong>Leads that need another attempt</strong>
        <ul>{jobStatus.failures.slice(0, 10).map((failure) => <li key={failure.lead_id}>Lead {failure.lead_id}: {failure.code}</li>)}</ul>
        {jobStatus.failures.length > 10 && <p>{jobStatus.failures.length - 10} additional failed leads are recorded.</p>}
      </div>}
      <p className="backfill-cost-note">This uses the workspace’s provider key. Estimated costs are approximate and may differ from the provider’s invoice.</p>
      {!terminal && <p className="backfill-note">You can close this window; background analysis will continue.</p>}
    </OperationsDialog>;
  }

  return <OperationsDialog title="AI analysis setup" className="team-modal openai-setup-prompt" showHeader={false} onClose={() => { if (!busy) onLater(); }}>
    <div className="team-modal-head"><div><span className="eyebrow">Optional workspace setup</span><h2 id="openai-setup-title">Connect an AI analysis provider</h2><p>Choose a provider and add its API key for this workspace. The key is encrypted, never shown again, and usage is billed to the selected provider account.</p></div></div>
    <form onSubmit={(event) => void submit(event)}>
      <label>Provider<select value={provider} onChange={(event) => chooseProvider(event.target.value)}>{availableCards.map((item) => <option value={item.provider} key={item.provider}>{item.display_name}</option>)}</select></label>
      {!isOpenAI && <label>Account label<input type="text" autoComplete="off" value={accountName} onChange={(event) => setAccountName(event.target.value)} placeholder={`${selectedCard.display_name} account`} /></label>}
      <label>{isOpenAI ? "OpenAI project API key" : `${selectedCard.display_name} API key`}<input type="password" autoComplete="new-password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} required aria-label={`${selectedCard.display_name} API key`} /></label>
      {selectedCard.supported_models.length > 0 && <label>Model<select value={model} onChange={(event) => setModel(event.target.value)}>{selectedCard.supported_models.map((item) => <option value={item} key={item}>{item}</option>)}</select></label>}
      {error && <p className="team-alert" role="alert">{error}</p>}
      <div className="team-modal-actions"><button className="button-quiet" type="button" onClick={onLater} disabled={busy}>Not now</button><button className="button-quiet" type="button" onClick={onSettings} disabled={busy}>Integration settings</button><button className="primary-button" type="submit" disabled={busy || !apiKey.trim() || (selectedCard.supported_models.length > 0 && !model)}>{phase === "saving" ? "Saving key…" : phase === "verifying" ? `Verifying with ${selectedCard.display_name}…` : phase === "backfilling" ? "Applying to existing leads…" : `Save and verify with ${selectedCard.display_name}`}</button></div>
    </form>
    <p className="openai-setup-fallback">If you skip this, leads still receive deterministic analysis. A verified workspace key takes priority when you add one.</p>
  </OperationsDialog>;
}
