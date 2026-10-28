import { useState } from "react";
import type { FormEvent } from "react";
import { OperationsDialog } from "./OperationsDialog";
import {
  addIntegrationCredential,
  backfillOpenAI,
  reconnectIntegration,
  testIntegration,
  updateOpenAIModel,
} from "../api";
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
  onLater,
  onSettings,
}: {
  card: IntegrationCard;
  cards: IntegrationCard[];
  onVerified: () => void;
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
  const isOpenAI = selectedCard.provider === "openai";

  function chooseProvider(nextProvider: string) {
    const nextCard = availableCards.find((item) => item.provider === nextProvider) ?? card;
    setProvider(nextProvider);
    setModel(nextCard.connection?.selected_model ?? nextCard.default_model ?? nextCard.supported_models[0] ?? "");
    setApiKey("");
    setError(null);
  }

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
      if (isOpenAI) {
        if (connection.selected_model !== model) await withTimeout(updateOpenAIModel(connection.id, model));
        setPhase("verifying");
        const result = await withTimeout(testIntegration(connection.id));
        if (!result.verified) throw new Error(result.message);
        setPhase("backfilling");
        await withTimeout(backfillOpenAI(connection.id), 5000);
      }
      onVerified();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : `${selectedCard.display_name} setup failed. Check the key and provider access.`);
    } finally {
      setBusy(false);
      setPhase(null);
    }
  }

  return <OperationsDialog title="AI analysis setup" className="team-modal openai-setup-prompt" showHeader={false} onClose={() => { if (!busy) onLater(); }}>
    <div className="team-modal-head"><div><span className="eyebrow">Optional workspace setup</span><h2 id="openai-setup-title">Connect an AI analysis provider</h2><p>Choose a provider and add its API key for this workspace. The key is encrypted, never shown again, and usage is billed to the selected provider account.</p></div></div>
    <form onSubmit={(event) => void submit(event)}>
      <label>Provider<select value={provider} onChange={(event) => chooseProvider(event.target.value)}>{availableCards.map((item) => <option value={item.provider} key={item.provider}>{item.display_name}</option>)}</select></label>
      {!isOpenAI && <label>Account label<input type="text" autoComplete="off" value={accountName} onChange={(event) => setAccountName(event.target.value)} placeholder={`${selectedCard.display_name} account`} /></label>}
      <label>{isOpenAI ? "OpenAI project API key" : `${selectedCard.display_name} API key`}<input type="password" autoComplete="new-password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} required aria-label={`${selectedCard.display_name} API key`} /></label>
      {selectedCard.supported_models.length > 0 && <label>Model<select value={model} onChange={(event) => setModel(event.target.value)}>{selectedCard.supported_models.map((item) => <option value={item} key={item}>{item}</option>)}</select></label>}
      {error && <p className="team-alert" role="alert">{error}</p>}
      <div className="team-modal-actions"><button className="button-quiet" type="button" onClick={onLater} disabled={busy}>Not now</button><button className="button-quiet" type="button" onClick={onSettings} disabled={busy}>Integration settings</button><button className="primary-button" type="submit" disabled={busy || !apiKey.trim() || (selectedCard.supported_models.length > 0 && !model)}>{phase === "saving" ? "Saving key…" : phase === "verifying" ? `Verifying with ${selectedCard.display_name}…` : phase === "backfilling" ? "Applying to existing leads…" : isOpenAI ? "Save and verify key" : "Save key"}</button></div>
    </form>
    <p className="openai-setup-fallback">If you skip this, leads still receive deterministic analysis. A verified workspace key takes priority when you add one.</p>
  </OperationsDialog>;
}
