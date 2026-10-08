import { useEffect, useState } from "react";

export function IntegrationAccountChoice({
  provider, accountName, busy, onContinue, onCancel, showActions = true, onReadyChange,
}: {
  provider: "calendly" | "salesforce";
  accountName?: string;
  busy: boolean;
  onContinue: () => void;
  onCancel: () => void;
  showActions?: boolean;
  onReadyChange?: (ready: boolean) => void;
}) {
  const [choice, setChoice] = useState<"current" | "different" | null>(null);
  const [switched, setSwitched] = useState(false);
  const name = provider === "salesforce" ? "Salesforce" : "Calendly";
  const loginUrl = provider === "salesforce" ? "https://login.salesforce.com" : "https://calendly.com/login";
  const ready = choice === "current" || (choice === "different" && switched);
  useEffect(() => { onReadyChange?.(ready); }, [ready, onReadyChange]);
  return <section className="integration-guide integration-account-choice" aria-label={`Choose ${name} account`}>
    <span className="eyebrow">Choose before connecting</span>
    <h3>Which {name} account do you want to use?</h3>
    {accountName && <p>Currently connected in Corral: <strong>{accountName}</strong>. Your browser may be signed in to a different account.</p>}
    <div className="account-choice-options">
      <label><input type="radio" name={`${provider}-account-choice`} checked={choice === "current"} onChange={() => setChoice("current")} disabled={busy} />
        {provider === "salesforce" ? "Use the current browser's Salesforce organization" : "Use the current browser's Calendly account"}</label>
      <label><input type="radio" name={`${provider}-account-choice`} checked={choice === "different"} onChange={() => setChoice("different")} disabled={busy} />
        Use another {name} account{provider === "salesforce" ? " or organization" : ""}</label>
    </div>
    {choice === "current" && provider === "salesforce" && <p>Authorize the same Salesforce organization where you installed the package.</p>}
    {choice === "different" && <div className="account-switch-steps">
      <p>Open {name}, sign out of the current account if necessary, and sign in to the account you want. Return here before continuing.</p>
      <a href={loginUrl} target="_blank" rel="noopener noreferrer">Open {name} to switch accounts</a>
      {provider === "calendly" && <p>Calendly uses its existing browser session and does not provide a forced account-picker option. Switching there first prevents connecting the wrong account.</p>}
      <label><input type="checkbox" checked={switched} onChange={(event) => setSwitched(event.target.checked)} disabled={busy} /> I have signed in to the account I want to connect</label>
    </div>}
    {showActions && <div className="card-actions">
      <button disabled={busy || !choice || (choice === "different" && !switched)} onClick={onContinue}>{busy ? "Connecting…" : `Continue to ${name}`}</button>
      <button className="button-quiet" disabled={busy} onClick={onCancel}>Cancel</button>
    </div>}
  </section>;
}
