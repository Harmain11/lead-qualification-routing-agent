import { useEffect, useState } from "react";
import type { FormEvent } from "react";

import {
  addIntegrationCredential,
  backfillReasoning,
  reconnectIntegration,
  testIntegration,
  updateReasoningModel,
} from "../api";
import type { IntegrationCard } from "../types";

function withTimeout<T>(promise: Promise<T>, milliseconds = 45000): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      reject(new Error("OpenAI verification timed out. Check the key, model, and backend connection, then try again."));
    }, milliseconds);
    promise.then(resolve, reject).finally(() => window.clearTimeout(timer));
  });
}

export function OpenAISetupPrompt({
  cards,
  onVerified,
  onLater,
  onSettings,
}: {
  cards: IntegrationCard[];
  onVerified: () => void;
  onLater: () => void;
  onSettings: () => void;
}) {
  const [provider, setProvider] = useState(cards[0]?.provider ?? "openai");
  const card = cards.find((item) => item.provider === provider) ?? cards[0];
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState(card?.connection?.selected_model ?? card?.default_model ?? card?.supported_models[0] ?? "");
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState<"saving" | "verifying" | "backfilling" | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setModel(card?.connection?.selected_model ?? card?.default_model ?? card?.supported_models[0] ?? "");
  }, [provider, cards]);

  if (!card) return null;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!card || !apiKey.trim() || !model) return;
    setBusy(true);
    setPhase("saving");
    setError(null);
    try {
      const connection = card.connection
        ? await withTimeout(reconnectIntegration(card.connection.id, { api_key: apiKey.trim() }))
        : await withTimeout(addIntegrationCredential(
          card.provider,
          `${card.display_name} account`,
          "api_key",
          card.required_scopes,
          { api_key: apiKey.trim() },
          { selected_model: model },
        ));
      setApiKey("");
      if (connection.selected_model !== model) await withTimeout(updateReasoningModel(connection.id, model));
      setPhase("verifying");
      const result = await withTimeout(testIntegration(connection.id));
      if (!result.verified) throw new Error(result.message);
      setPhase("backfilling");
      await withTimeout(backfillReasoning(connection.id), 5000);
      onVerified();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : `${card.display_name} verification failed. Check the key and model access.`);
    } finally {
      setBusy(false);
      setPhase(null);
    }
  }

  return <div className="team-modal-backdrop" role="presentation">
    <section className="team-modal openai-setup-prompt" role="dialog" aria-modal="true" aria-labelledby="openai-setup-title">
      <div className="team-modal-head"><div><span className="eyebrow">Optional workspace setup</span><h2 id="openai-setup-title">Choose an AI provider</h2><p>Connect OpenAI, Google Gemini, or Anthropic Claude for lead analysis. Choose which provider and API key to use for this workspace; provider usage is billed to its own account.</p></div></div>
      <form onSubmit={(event) => void submit(event)}>
        <label>AI provider<select value={provider} onChange={(event) => { setProvider(event.target.value); setApiKey(""); }}>{cards.map((item) => <option value={item.provider} key={item.provider}>{item.display_name}</option>)}</select></label>
        <label>{card.display_name} API key<input type="password" autoComplete="new-password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} required aria-label={`${card.display_name} API key`} /></label>
        <label>Model<select value={model} onChange={(event) => setModel(event.target.value)}>{card.supported_models.map((item) => <option value={item} key={item}>{item}</option>)}</select></label>
        <p>After verification, leads not yet analyzed by this provider are queued for analysis. This uses your provider API quota and may incur charges.</p>
        {error && <p className="team-alert" role="alert">{error}</p>}
        <div className="team-modal-actions"><button className="button-quiet" type="button" onClick={onLater} disabled={busy}>Not now</button><button className="button-quiet" type="button" onClick={onSettings} disabled={busy}>Integration settings</button><button className="primary-button" type="submit" disabled={busy || !apiKey.trim() || !model}>{phase === "saving" ? "Saving key…" : phase === "verifying" ? `Verifying with ${card.display_name}…` : phase === "backfilling" ? "Applying to existing leads…" : `Save and verify with ${card.display_name}`}</button></div>
      </form>
      <p className="openai-setup-fallback">If you skip this, leads still receive deterministic analysis. A verified workspace provider takes priority when you connect one.</p>
    </section>
  </div>;
}
