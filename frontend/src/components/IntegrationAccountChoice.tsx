import { useState } from "react";

export function IntegrationAccountChoice({
  provider, accountName, busy, onContinue, onCancel,
}: {
  provider: "calendly" | "slack";
  accountName?: string;
  busy: boolean;
  onContinue: () => void;
  onCancel: () => void;
}) {
  const [choice, setChoice] = useState<"current" | "different" | null>(null);
  const [switched, setSwitched] = useState(false);
  const name = provider === "slack" ? "Slack" : "Calendly";
  const loginUrl = provider === "slack" ? "https://slack.com/signin" : "https://calendly.com/login";
  return <section className="integration-guide integration-account-choice" aria-label={`Choose ${name} account`}>
    <span className="eyebrow">Choose before connecting</span>
    <h3>Which {name} account do you want to use?</h3>
    {accountName && <p>Currently connected in Corral: <strong>{accountName}</strong>. Your browser may be signed in to a different account.</p>}
    <div className="account-choice-options">
      <label><input type="radio" name={`${provider}-account-choice`} checked={choice === "current"} onChange={() => setChoice("current")} />
        {provider === "slack" ? "Choose from signed-in Slack workspaces" : "Use the current browser's Calendly account"}</label>
      <label><input type="radio" name={`${provider}-account-choice`} checked={choice === "different"} onChange={() => setChoice("different")} />
        Use another {name} account</label>
    </div>
    {choice === "current" && provider === "slack" && <p>Choose the workspace in Slack's authorization screen before allowing access. Corral does not preselect a workspace.</p>}
    {choice === "different" && <div className="account-switch-steps">
      <p>Open {name}, sign out of the current account if necessary, and sign in to the account you want. Return here before continuing.</p>
      <a href={loginUrl} target="_blank" rel="noopener noreferrer">Open {name} to switch accounts</a>
      {provider === "calendly" && <p>Calendly uses its existing browser session and does not provide a forced account-picker option. Switching there first prevents connecting the wrong account.</p>}
      <label><input type="checkbox" checked={switched} onChange={(event) => setSwitched(event.target.checked)} /> I have signed in to the account I want to connect</label>
    </div>}
    <div className="card-actions">
      <button disabled={busy || !choice || (choice === "different" && !switched)} onClick={onContinue}>{busy ? "Connecting…" : `Continue to ${name}`}</button>
      <button className="button-quiet" disabled={busy} onClick={onCancel}>Cancel</button>
    </div>
  </section>;
}
