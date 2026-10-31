import { useState } from "react";

const steps = [
  "Create or choose a Slack app for your workspace.",
  "Turn on a bot user for the app.",
  "Add the required permissions: chat:write and channels:read.",
  "Add im:write only if your team wants Corral to start direct messages.",
  "Install or reinstall the app in your workspace after changing permissions.",
  "Installing Corral gives it workspace access. Invite the Corral bot to each private channel it should use; Slack does not grant private-channel access automatically.",
  "Copy the Bot User OAuth Token into Corral’s secure Slack token field.",
  "Choose a destination channel, then select Verify connection.",
];

export function SlackSetupGuide({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [copied, setCopied] = useState<string | null>(null);

  async function copySafeValue(label: string, value: string) {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(`${label} copied`);
    } catch {
      setCopied("Clipboard access is unavailable. Select and copy the scope text.");
    }
  }

  return <details open={open} className="integration-guide slack-setup-guide">
    <summary aria-expanded={open} onClick={(event) => {
      event.preventDefault();
      onOpenChange(!open);
    }}>
      <span className="slack-guide-summary-title"><i aria-hidden="true">›</i><span><strong>How to connect Slack</strong><small>Step-by-step setup, scopes, and safe token handling</small></span></span>
    </summary>
    <div className="slack-guide-content">
      <p>Follow these steps to connect Corral to Slack.</p>
      <ol className="slack-setup-checklist">
        {steps.map((step, index) => <li className={index === 5 ? "slack-private-channel-step" : undefined} key={step}>
          <span className="slack-step-number" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
          <p className="slack-step-text">{step}</p>
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
