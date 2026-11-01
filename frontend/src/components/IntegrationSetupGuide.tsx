import { Icon } from "../v3/ui/Icon";
import type { IntegrationCard } from "../types";

const AUTH_LABELS: Record<string, string> = {
  oauth2: "Sign in with the provider",
  api_key: "API key",
  manual: "Credentials you paste in",
  token: "Access token",
  webhook: "Webhook",
};

const label = (value: string) => AUTH_LABELS[value] ?? value.replaceAll("_", " ").replace(/^\w/, (c) => c.toUpperCase());

/** Splits provider help text into short steps so it reads as a procedure, not a paragraph. */
function stepsFrom(text: string): string[] {
  return text
    .split(/(?<=[.!?])\s+(?=[A-Z0-9“"])/)
    .map((part) => part.trim())
    .filter(Boolean);
}

/**
 * The setup guide for one integration: what to do in order, what Corral will be
 * allowed to do, and where to look when it does not connect.
 */
export function IntegrationSetupGuide({ card, extra }: { card: IntegrationCard; extra?: React.ReactNode }) {
  const steps = stepsFrom(card.credential_help || "");
  const auth = card.supported_auth.map(label);
  return (
    <div className="sg">
      {steps.length > 0 && (
        <section className="sg-block" aria-label={`Set up ${card.display_name}`}>
          <ol className="sg-steps">
            {steps.map((step, index) => (
              <li key={index}>
                <span className="sg-num" aria-hidden="true">{index + 1}</span>
                <p>{step}</p>
              </li>
            ))}
          </ol>
        </section>
      )}
      <section className="sg-block" aria-label="What Corral needs">
        <dl className="sg-facts">
          {auth.length > 0 && <div><dt>Connects with</dt><dd>{auth.join(" or ")}</dd></div>}
          {card.dashboard_path && <div><dt>Where to find it</dt><dd>{card.dashboard_path}</dd></div>}
          {card.required_scopes.length > 0 && (
            <div className="sg-wide"><dt>Permissions requested</dt><dd><ul className="sg-scopes">{card.required_scopes.map((scope) => <li key={scope}>{scope}</li>)}</ul></dd></div>
          )}
        </dl>
      </section>
      {card.documentation_url && (
        <a className="sg-doc" href={card.documentation_url} target="_blank" rel="noopener noreferrer">
          Open the official documentation<Icon name="external" size={14} />
        </a>
      )}
      {extra}
    </div>
  );
}
