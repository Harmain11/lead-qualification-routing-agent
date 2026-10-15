import { useState } from "react";

const steps = [
  "Create or select a Slack app for the chosen workspace.",
  "Enable a bot user for the app.",
  "Under OAuth & Permissions, add the minimum bot scopes: chat:write and channels:read.",
  "Add im:write only if the team wants Corral to start direct messages.",
  "Install or reinstall the app to the chosen workspace after changing scopes.",
  "Copy the Bot User OAuth Token into Corral’s secure Slack token field.",
  "Select a destination channel, invite the bot to any private channel, then use Verify connection.",
];

export function SlackSetupGuide() {
  const [completed, setCompleted] = useState<boolean[]>(steps.map(() => false));
  const [copied, setCopied] = useState<string | null>(null);
  const done = completed.filter(Boolean).length;

  async function copySafeValue(label: string, value: string) {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(`${label} copied`);
    } catch {
      setCopied("Clipboard access is unavailable. Select and copy the scope text.");
    }
  }

  return <details className="integration-guide slack-setup-guide">
    <summary>How to connect Slack <span>{done}/{steps.length} complete</span></summary>
    <div className="slack-guide-content">
      <p>Set up a Slack app, then connect its bot token to Corral. Check off each step as you go.</p>
      <ol className="slack-setup-checklist">
        {steps.map((step, index) => <li key={step}>
          <div className="slack-check-row">
            <input id={`slack-setup-step-${index}`} type="checkbox" checked={completed[index]} onChange={(event) => setCompleted((current) => current.map((value, item) => item === index ? event.target.checked : value))} />
            <label htmlFor={`slack-setup-step-${index}`}>{step}</label>
          </div>
          {index === 2 && <div className="slack-scope-copy"><span><b>Minimum required bot scopes</b><code>chat:write, channels:read</code></span><button type="button" className="button-quiet" onClick={() => void copySafeValue("Required scopes", "chat:write, channels:read")}>Copy scopes</button></div>}
          {index === 3 && <div className="slack-scope-copy optional"><span><b>Optional · direct messages only</b><code>im:write</code></span><button type="button" className="button-quiet" onClick={() => void copySafeValue("Optional scope", "im:write")}>Copy scope</button></div>}
        </li>)}
      </ol>
      {copied && <span className="slack-guide-copy-status" role="status" aria-live="polite">{copied}</span>}
      <p className="slack-token-warning"><strong>Token safety:</strong> Paste tokens only into Corral’s secure Slack token field. Never put a token in chat, a note, or a copied scope value. Corral never asks for <code>chat:write.public</code> by default.</p>
      <div className="slack-delivery-explainer">
        <strong>Where notifications go</strong>
        <p>Normal lead alerts go to the selected channel. A direct message is sent only when a notification rule explicitly selects a recipient. Corral needs that person’s Slack member ID and the app needs <code>chat:write</code> plus <code>im:write</code> to open or resume the one-to-one bot conversation.</p>
        <p>If Slack cannot deliver a direct message, Corral reports the reason and does not silently broadcast it to the channel. A channel fallback should be used only when explicitly selected for that notification.</p>
      </div>
    </div>
  </details>;
}
