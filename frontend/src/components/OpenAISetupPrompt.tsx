import { useState } from "react";
import type { FormEvent } from "react";

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
      reject(new Error("OpenAI verification timed out. Check the key, model, and backend connection, then try again."));
    }, milliseconds);
    promise.then(resolve, reject).finally(() => window.clearTimeout(timer));
  });
}

export function OpenAISetupPrompt({
  card,
  onVerified,
  onLater,
  onSettings,
}: {
  card: IntegrationCard;
  onVerified: () => void;
  onLater: () => void;
  onSettings: () => void;
}) {
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState(card.connection?.selected_model ?? card.default_model ?? card.supported_models[0] ?? "");
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState<"saving" | "verifying" | "backfilling" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!apiKey.trim() || !model) return;
    setBusy(true);
    setPhase("saving");
    setError(null);
    try {
      const connection = card.connection
        ? await withTimeout(reconnectIntegration(card.connection.id, { api_key: apiKey.trim() }))
        : await withTimeout(addIntegrationCredential(
          "openai",
          "OpenAI project",
          "api_key",
          card.required_scopes,
          { api_key: apiKey.trim() },
          { selected_model: model },
        ));
      setApiKey("");
      if (connection.selected_model !== model) await withTimeout(updateOpenAIModel(connection.id, model));
      setPhase("verifying");
      const result = await withTimeout(testIntegration(connection.id));
      if (!result.verified) throw new Error(result.message);
      setPhase("backfilling");
      await withTimeout(backfillOpenAI(connection.id), 5000);
      onVerified();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "OpenAI verification failed. Check the key and project access.");
    } finally {
      setBusy(false);
      setPhase(null);
    }
  }

  return <div className="team-modal-backdrop" role="presentation">
    <section className="team-modal openai-setup-prompt" role="dialog" aria-modal="true" aria-labelledby="openai-setup-title">
      <div className="team-modal-head"><div><span className="eyebrow">Optional workspace setup</span><h2 id="openai-setup-title">Analyze leads with OpenAI</h2><p>Add a project API key to use OpenAI for this workspace. The key is encrypted, never shown again, and API usage is billed to your OpenAI account.</p></div></div>
      <form onSubmit={(event) => void submit(event)}>
        <label>OpenAI project API key<input type="password" autoComplete="new-password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} required aria-label="OpenAI project API key" /></label>
        <label>Model<select value={model} onChange={(event) => setModel(event.target.value)}>{card.supported_models.map((item) => <option value={item} key={item}>{item}</option>)}</select></label>
        {error && <p className="team-alert" role="alert">{error}</p>}
        <div className="team-modal-actions"><button className="button-quiet" type="button" onClick={onLater} disabled={busy}>Not now</button><button className="button-quiet" type="button" onClick={onSettings} disabled={busy}>Integration settings</button><button className="primary-button" type="submit" disabled={busy || !apiKey.trim() || !model}>{phase === "saving" ? "Saving key…" : phase === "verifying" ? "Verifying with OpenAI…" : phase === "backfilling" ? "Applying to existing leads…" : "Save and verify key"}</button></div>
      </form>
      <p className="openai-setup-fallback">If you skip this, leads still receive deterministic analysis. A verified workspace key takes priority when you add one.</p>
    </section>
  </div>;
}
